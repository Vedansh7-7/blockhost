"""BlockHost logic tests. No Google account needed: Drive is replaced by an in-memory fake.

    python tests/test_logic.py           # crypto + lock / heartbeat / fencing / race
    python tests/test_logic.py --e2e     # also boots a real Minecraft server (needs Java + internet)
"""
import base64
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import uuid
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
SCRATCH = tempfile.mkdtemp(prefix="bh_test_")
os.environ["BLOCKHOST_HOME"] = os.path.join(SCRATCH, "home")  # must be set before importing config

from core import config, crypto, mcserver, system, session as sess_mod  # noqa: E402

failures = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name, flush=True)
    if not cond:
        failures.append(name)


def expect(exc, fn, *a):
    try:
        fn(*a)
    except exc as e:
        return e
    return None


# ======================= crypto =======================
key = os.urandom(32)
src = os.path.join(SCRATCH, "plain.bin")
enc, dec = src + ".enc", src + ".dec"
for size in (0, 10, crypto.CHUNK, crypto.CHUNK * 2 + 123):
    data = os.urandom(size)
    with open(src, "wb") as f:
        f.write(data)
    crypto.encrypt_file(src, enc, key)
    crypto.decrypt_file(enc, dec, key)
    check(f"crypto roundtrip {size} bytes", open(dec, "rb").read() == data)

raw = bytearray(open(enc, "rb").read())
raw[100] ^= 1
open(enc, "wb").write(raw)
check("crypto detects tampering", expect(crypto.CorruptArchive, crypto.decrypt_file, enc, dec, key) is not None)
crypto.encrypt_file(src, enc, key)
open(enc, "wb").write(open(enc, "rb").read()[: 12 + 4 + crypto.CHUNK + 16])
check("crypto detects truncation", expect(crypto.CorruptArchive, crypto.decrypt_file, enc, dec, key) is not None)
check("crypto rejects non-archive", expect(crypto.CorruptArchive, crypto.decrypt_file, src, dec, key) is not None)

g = crypto.seal_group({"a": 1}, "correct horse")
check("group file opens with password", crypto.open_group(g, "correct horse") == {"a": 1})
check("group file rejects wrong password", expect(crypto.BadPassword, crypto.open_group, g, "nope") is not None)
check(f"local cache protect roundtrip ({sys.platform})",
      system.unprotect_local(system.protect_local(b"secret")) == b"secret")

# ======================= fake Drive =======================


class FakeDrive:
    files, mtimes, names = {}, {}, {}
    lock = threading.Lock()

    def __init__(self, *a):
        pass

    def now(self):
        return time.time()

    def create_json(self, folder, name, data):
        fid = uuid.uuid4().hex
        with self.lock:
            self.files[fid], self.mtimes[fid], self.names[fid] = json.dumps(data), time.time(), name
        return fid

    def read_json(self, fid):
        with self.lock:
            return json.loads(self.files[fid]), self.mtimes[fid], time.time()

    def write_json(self, fid, data):
        with self.lock:
            self.files[fid], self.mtimes[fid] = json.dumps(data), time.time()

    def upload_file(self, folder, name, path, progress=None):
        fid = uuid.uuid4().hex
        with self.lock:
            self.files[fid], self.mtimes[fid], self.names[fid] = open(path, "rb").read(), time.time(), name
        return fid

    def download_file(self, fid, path, progress=None):
        with open(path, "wb") as f:
            f.write(self.files[fid])

    def delete(self, fid):
        with self.lock:
            self.files.pop(fid, None)


sess_mod.Drive = FakeDrive
config.CLAIM_SETTLE_SEC = 0.3
config.STALE_SEC = 2
config.HEARTBEAT_SEC = 0.5
config.ensure_dirs()

fd = FakeDrive()
state_id = fd.create_json("f", "state.json", {"v": 1, "state": "idle", "gen": 0, "mc_version": "26.3",
                                               "online_mode": False, "world": {"current": None, "previous": None}})
group = {"client_id": "x", "client_secret": "y", "refresh_token": "z", "folder_id": "f", "state_id": state_id,
         "world_key": base64.b64encode(os.urandom(32)).decode(), "mc_version": "26.3", "group_name": "Test"}
events = []


def emit(kind, data):
    events.append((kind, data))


def laptop(name):
    s = sess_mod.Session(group, emit)
    s.me = name
    return s


def state():
    return fd.read_json(state_id)[0]


# ======================= lock / heartbeat / fencing =======================
A, B = laptop("LAPTOP-A"), laptop("LAPTOP-B")
A._claim(False)
check("A claims: running, gen 1", state()["state"] == "running" and state()["gen"] == 1 and state()["host"] == "LAPTOP-A")
e = expect(sess_mod.Busy, B._claim, False)
check("B refused while A is alive", e is not None and e.info["host"] == "LAPTOP-A" and not e.info["stale"])

snap = os.path.join(SCRATCH, "snapA")
os.makedirs(os.path.join(snap, "world"))
open(os.path.join(snap, "world", "level.dat"), "w").write("A1")
check("A uploads a world", A._upload_world(snap, "test") is True)
cur = state()["world"]["current"]
check("state points at A's world (gen 1)", cur and cur["gen"] == 1 and cur["saved_by"] == "LAPTOP-A")

A._stop_evt.clear()
threading.Thread(target=A._heartbeat, daemon=True).start()
time.sleep(2.5)
check("takeover refused while heartbeat is fresh", expect(sess_mod.Busy, B._claim, True) is not None)
A._stop_evt.set()
time.sleep(2.6)
e = expect(sess_mod.Busy, B._claim, False)
check("stale host needs explicit takeover", e is not None and e.info["stale"] is True)
B._claim(True)
check("B takes over (gen 2)", state()["host"] == "LAPTOP-B" and state()["gen"] == 2)

open(os.path.join(snap, "world", "level.dat"), "w").write("A2-late")
r = A._upload_world(snap, "late")
check("A's late save is fenced", r is False and A.fenced)
check("A's late save did not replace the world", state()["world"]["current"]["id"] == cur["id"])
check("A's late save kept as CONFLICT file", any(n.startswith("CONFLICT-") for n in FakeDrive.names.values()))

B._upload_world(snap, "b1")
w1 = state()["world"]
B._upload_world(snap, "b2")
w2 = state()["world"]
check("previous = last current", w2["previous"]["id"] == w1["current"]["id"])
check("older save deleted (only 2 kept)", w1["previous"]["id"] not in FakeDrive.files)
B._release()
check("release: idle, gen kept", state()["state"] == "idle" and state()["gen"] == 2)

C, D = laptop("LAPTOP-C"), laptop("LAPTOP-D")
res = {}


def go(s):
    try:
        s._claim(False)
        res[s.me] = "won"
    except sess_mod.Busy:
        res[s.me] = "lost"


ts = [threading.Thread(target=go, args=(s,)) for s in (C, D)]
[t.start() for t in ts]
[t.join() for t in ts]
check(f"simultaneous START: exactly one winner {res}", sorted(res.values()) == ["lost", "won"])
(C if res["LAPTOP-C"] == "won" else D)._release()

before = state()["world"]
C.restore_previous()
check("restore previous swaps the two saves", state()["world"]["current"]["id"] == before["previous"]["id"])

# ======================= world settings / new world (no server needed) =======================
W = laptop("LAPTOP-W")
clean = W.set_world_settings({"difficulty": "hard", "gamemode": "bogus", "max_players": "999",
                              "motd": "line1\nline2", "pvp": False, "keep_inventory": True})
check("settings validated (bad gamemode -> survival, max 100, one-line motd)",
      clean["gamemode"] == "survival" and clean["max_players"] == 100 and clean["motd"] == "line1 line2")
check("settings stored in Drive", state()["world_settings"]["difficulty"] == "hard")
W.request_new_world("  blockhost  ", "flat")
check("new world queued (seed trimmed)", state()["new_world"]["seed"] == "blockhost"
      and state()["new_world"]["level_type"] == "flat")
check("bad world type rejected", expect(RuntimeError, W.request_new_world, "x", "nether") is not None)
W.cancel_new_world()
check("new world cancelled", "new_world" not in state())
W._claim(False)
check("settings refused while someone hosts", expect(RuntimeError, laptop("LAPTOP-V").set_world_settings, {}) is not None)
W._release()

events.clear()
laptop("LAPTOP-E").start({"ram_gb": 1, "autosave_min": 20, "eula": False})
check("start without EULA asks for it and does not claim",
      any(k == "need_eula" for k, _ in events) and state()["state"] == "idle")

# ======================= end-to-end with a real server =======================
if "--e2e" in sys.argv:
    mcserver.add_firewall_rule = lambda port: True     # no admin prompt in tests
    mcserver.firewall_rule_exists = lambda port: True
    _prep = mcserver.prepare_files

    def prep_on_test_port(*a, **k):                    # never collide with a real server on 25565
        w = _prep(*a, **k)
        props = os.path.join(config.SERVER_DIR, "server.properties")
        lines = [l for l in open(props, encoding="utf-8").read().splitlines() if not l.startswith("server-port=")]
        open(props, "w", encoding="utf-8").write(chr(10).join(lines + ["server-port=25599"]) + chr(10))
        return w
    mcserver.prepare_files = prep_on_test_port
    settings = {"ram_gb": 2, "autosave_min": 20, "eula": True}

    E = laptop("LAPTOP-E")
    t0 = time.time()
    err = expect(RuntimeError, E.start, settings)      # current save is a fake 'level.dat' -> corrupt
    check(f"corrupt world fails fast ({time.time() - t0:.0f}s)", err is not None and "damaged" in str(err)
          and time.time() - t0 < 120)
    check("lock released after failed start", state()["state"] == "idle")

    st = state()
    st["world"] = {"current": None, "previous": None}
    fd.write_json(state_id, st)
    config.save_session(None)
    E = laptop("LAPTOP-E")
    t0 = time.time()
    E.start(settings)
    check(f"real server boots ({time.time() - t0:.0f}s)", E.phase == "running" and E.server.running())
    props = open(os.path.join(config.SERVER_DIR, "server.properties"), encoding="utf-8").read()
    check("group online_mode=false applied", "online-mode=false" in props)
    time.sleep(3)
    check("save while playing", E.save_now("test") is True)
    check("server still running after the save", E.server.running())
    E.stop()
    st = state()
    check("stop: idle and world uploaded", E.phase == "idle" and st["state"] == "idle"
          and st["world"]["current"]["saved_by"] == "LAPTOP-E")
    blob = os.path.join(SCRATCH, "check.bhw")
    open(blob, "wb").write(FakeDrive.files[st["world"]["current"]["id"]])
    crypto.decrypt_file(blob, blob + ".zip", config.world_key(group))
    names = [n.replace("\\", "/") for n in zipfile.ZipFile(blob + ".zip").namelist()]
    check("uploaded archive contains world/level.dat", "world/level.dat" in names)
    check("crash marker cleared", config.load_session() is None)
    check("game rules applied live (pvp off, keep_inventory on)",
          any("pvp is now set to false" in str(d.get("line")) for k, d in events if k == "console") and
          any("keep_inventory is now set to true" in str(d.get("line")) for k, d in events if k == "console"))
    first_seed = state().get("world_info", {}).get("seed")
    check(f"seed captured ({first_seed})", bool(first_seed) and first_seed.lstrip("-").isdigit())

    # ---- NEW WORLD with a chosen seed and type ----
    old_world = state()["world"]["current"]
    E.request_new_world("blockhost", "flat")
    E = laptop("LAPTOP-E")
    E.start(settings)
    props = open(os.path.join(config.SERVER_DIR, "server.properties"), encoding="utf-8").read()
    check("new world: flat type + seed written", "level-type=minecraft" in props and ":flat" in props
          and "level-seed=blockhost" in props)
    new_seed = E.model.get("world_info", {}).get("seed")
    check(f"new world has a different seed ({new_seed})", bool(new_seed) and new_seed.lstrip("-").isdigit()
          and new_seed != first_seed)
    E.stop()
    w = state()["world"]
    check("old world kept as archive, new_world cleared", w["archive"]["id"] == old_world["id"]
          and w["previous"] is None and "new_world" not in state())
    E.restore_archive()
    check("switch back: old world is current again, new one archived",
          state()["world"]["current"]["id"] == old_world["id"] and state()["world"]["archive"]["id"] == w["current"]["id"])

shutil.rmtree(SCRATCH, ignore_errors=True)
print("\n" + ("ALL PASSED" if not failures else f"{len(failures)} FAILED: {failures}"))
sys.exit(1 if failures else 0)
