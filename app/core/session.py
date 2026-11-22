"""The hosting session: lock (state + gen + nonce), heartbeat, world sync, safe stop.

state.json in Drive (the only shared truth):
  state      "idle" | "running"
  gen        +1 every time someone claims the server (fencing counter)
  nonce      random id of the current claim; only its owner may save over the world
  host, ip, port, players, mc_version
  world      {"current": {...}, "previous": {...}}  (encrypted archives, 2 kept)
Heartbeat = state.json's modifiedTime on Google's clock, refreshed every minute.
"""
import os
import platform
import re
import secrets
import shutil
import threading
import time
import traceback
import zipfile

from . import config, crypto, mcserver
from .drive import Drive, DriveError


class Busy(Exception):
    def __init__(self, info):
        super().__init__("busy")
        self.info = info


class Session:
    def __init__(self, group, emit):
        self.group = group
        self.emit = emit  # emit(kind, data) -> UI
        self.drive = Drive(group["client_id"], group["client_secret"], group["refresh_token"])
        self.key = config.world_key(group)
        self.me = f"{platform.node()} ({os.environ.get('USERNAME', '?')})"
        self.model = None       # our copy of state.json while we own it
        self.server = None
        self.fenced = False     # True if someone else took the lock from us
        self.unsaved = False    # world on disk newer than Drive
        self.phase = "idle"     # idle | starting | running | stopping | saving
        self._state_lock = threading.Lock()
        self._upload_lock = threading.Lock()
        self._stop_evt = threading.Event()
        self._last_save = None
        self._new_world = None  # set while hosting a freshly generated world until its first save

    # ---------------- helpers ----------------
    def log(self, msg, level="info"):
        self.emit("log", {"text": msg, "level": level})
        try:
            with open(config.LOG_FILE, "a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
        except OSError:
            pass

    def set_phase(self, p):
        self.phase = p
        self.emit("phase", {"phase": p})

    def read_state(self):
        return self.drive.read_json(self.group["state_id"])

    def status(self):
        """Public status for the UI (works whether or not we host)."""
        data, mtime, now = self.read_state()
        age = now - mtime
        st = data.get("state", "idle")
        mine = self.model is not None and data.get("nonce") == self.model.get("nonce")
        if st == "running" and not mine and age > config.STALE_SEC:
            st = "stale"
        cur = (data.get("world") or {}).get("current")
        return {
            "state": st, "mine": mine, "host": data.get("host"), "ip": data.get("ip", []),
            "port": data.get("port", 25565), "players": data.get("players", []),
            "gen": data.get("gen", 0), "heartbeat_age": int(age), "mc_version": data.get("mc_version"),
            "world": cur and {"gen": cur.get("gen"), "saved_by": cur.get("saved_by"),
                              "age": int(now - cur.get("saved_at", now)), "size": cur.get("size")},
            "has_previous": bool((data.get("world") or {}).get("previous")),
            "has_archive": bool((data.get("world") or {}).get("archive")),
            "world_settings": mcserver.clean_world_settings(data.get("world_settings")),
            "new_world": data.get("new_world"),
            "seed": (data.get("world_info") or {}).get("seed"),
        }

    # ---------------- lock ----------------
    def _claim(self, takeover):
        data, mtime, now = self.read_state()
        marker = config.load_session()
        if data.get("state") == "running":
            own_crash = marker and marker.get("nonce") == data.get("nonce")
            stale = now - mtime > config.STALE_SEC
            if not own_crash and not (stale and takeover):
                raise Busy({"host": data.get("host"), "ip": data.get("ip", []), "port": data.get("port"),
                            "stale": stale, "age": int(now - mtime)})
            if not own_crash:
                self.log(f"Taking over from {data.get('host')} (no heartbeat for {int((now - mtime) / 60)} min).", "warn")
        nonce = secrets.token_hex(8)
        model = dict(data)
        model.update(state="running", gen=int(data.get("gen", 0)) + 1, nonce=nonce, host=self.me,
                     ip=mcserver.lan_ips(), port=mcserver.server_port(), players=[], started=now)
        self.drive.write_json(self.group["state_id"], model)
        self.log("Claimed the server. Making sure nobody else clicked at the same moment...")
        time.sleep(config.CLAIM_SETTLE_SEC)
        check, _, _ = self.read_state()
        if check.get("nonce") != nonce:
            raise Busy({"host": check.get("host"), "ip": check.get("ip", []), "port": check.get("port"),
                        "stale": False, "age": 0, "race": True})
        self.model = model
        return data, marker

    def _still_mine(self):
        data, _, _ = self.read_state()
        return data.get("nonce") == self.model["nonce"], data

    def _write_model(self):
        with self._state_lock:
            ok, _ = self._still_mine()
            if not ok:
                self._on_fenced()
                return False
            self.drive.write_json(self.group["state_id"], self.model)
            return True

    def _on_fenced(self):
        if not self.fenced:
            self.fenced = True
            self.log("Someone else took over the server lock! Your saves will be kept as a conflict copy, "
                     "not over their world. Stop your server.", "error")
            self.emit("fenced", {})

    # ---------------- heartbeat ----------------
    def _heartbeat(self):
        while not self._stop_evt.wait(config.HEARTBEAT_SEC):
            if self.fenced:
                return
            try:
                if self.server:
                    self.model["players"] = sorted(list(self.server.players))
                self._write_model()
                self.emit("heartbeat", {"ok": True})
            except Exception as e:  # network blips must never touch the game
                self.emit("heartbeat", {"ok": False, "err": str(e)[:120]})

    # ---------------- world archive ----------------
    def _pack(self, folder, out_zip):
        with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=3) as z:
            for root, _, files in os.walk(folder):
                for fn in files:
                    full = os.path.join(root, fn)
                    z.write(full, os.path.relpath(full, folder))

    def _upload_world(self, snap_dir, label):
        """Zip + encrypt + upload a world snapshot, then point state.json at it."""
        with self._upload_lock:
            self.set_busy(f"Saving to Drive ({label})...")
            z = os.path.join(config.TMP_DIR, "up.zip")
            enc = os.path.join(config.TMP_DIR, "up.bhw")
            try:
                self._pack(snap_dir, z)
                crypto.encrypt_file(z, enc, self.key)
                size = os.path.getsize(enc)
                ok, _ = self._still_mine()
                if not ok or self.fenced:
                    self._on_fenced()
                    name = f"CONFLICT-{self.me}-{time.strftime('%Y%m%d-%H%M')}.bhw"
                    self.drive.upload_file(self.group["folder_id"], name, enc)
                    self.log(f"Saved your world as a separate conflict file in Drive: {name}", "warn")
                    return False
                fid = self.drive.upload_file(self.group["folder_id"], f"world-gen{self.model['gen']}-{int(time.time())}.bhw",
                                             enc, progress=lambda a, b: self.emit("progress", {"done": a, "total": b}))
                with self._state_lock:
                    ok, data = self._still_mine()
                    if not ok:
                        self._on_fenced()
                        self.drive.delete(fid)
                        return False
                    world = data.get("world") or {}
                    entry = {"id": fid, "gen": self.model["gen"], "size": size, "saved_by": self.me,
                             "saved_at": self.drive.now()}
                    if self._new_world is not None:
                        # First save of a brand-new world: the old world becomes the "archive"
                        # (kept until the next reset), and its previous save is dropped.
                        old_cur, old_arch = world.get("current"), world.get("archive")
                        self.model["world"] = {"current": entry, "previous": None,
                                               "archive": old_cur or old_arch}
                        self.model.pop("new_world", None)
                        drop = [world.get("previous")] + ([old_arch] if old_cur else [])
                        self._new_world = None
                    else:
                        self.model["world"] = {"current": entry, "previous": world.get("current"),
                                               "archive": world.get("archive")}
                        drop = [world.get("previous")]
                    self.drive.write_json(self.group["state_id"], self.model)
                for old in drop:
                    if old and old.get("id") != fid:
                        self.drive.delete(old["id"])
                self._last_save = time.time()
                self.log(f"World saved to Drive ({label}, {size / 1e6:.1f} MB).", "ok")
                self.emit("saved", {"at": self._last_save})
                return True
            finally:
                for p in (z, enc):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
                self.set_busy(None)

    def set_busy(self, text):
        self.emit("busy", {"text": text})

    def _download_world(self, entry):
        enc = os.path.join(config.TMP_DIR, "down.bhw")
        z = os.path.join(config.TMP_DIR, "down.zip")
        try:
            self.log(f"Downloading world (saved by {entry.get('saved_by')})...")
            self.drive.download_file(entry["id"], enc, progress=lambda a, b: self.emit("progress", {"done": a, "total": b}))
            crypto.decrypt_file(enc, z, self.key)
            for item in config.WORLD_ITEMS:
                p = os.path.join(config.SERVER_DIR, item)
                if os.path.isdir(p):
                    shutil.rmtree(p)
                elif os.path.isfile(p):
                    os.remove(p)
            with zipfile.ZipFile(z) as zf:
                zf.extractall(config.SERVER_DIR)
        finally:
            for p in (enc, z):
                try:
                    os.remove(p)
                except OSError:
                    pass

    # ---------------- start ----------------
    def start(self, settings, takeover=False):
        if not settings.get("eula"):
            self.emit("need_eula", {})
            return
        self.set_phase("starting")
        try:
            data, _, _ = self.read_state()
            version = data.get("mc_version") or self.group.get("mc_version")
            java_major = mcserver.ensure_jar(version, self.log)
            java = mcserver.find_java(java_major)
            if not java:
                self.emit("need_java", {"major": java_major})
                raise RuntimeError(f"Java {java_major} is not installed.")
            online = bool(data.get("online_mode", False))  # groups created before this option were offline
            mcserver.prepare_files(online, data.get("world_settings"))
            port = mcserver.server_port()
            if not mcserver.firewall_rule_exists(port):
                self.log("Asking Windows to allow friends to connect (one-time admin prompt)...")
                mcserver.add_firewall_rule(port)

            prev, marker = self._claim(takeover)
            cur = (prev.get("world") or {}).get("current")
            local_newer = bool(marker and marker.get("dirty") and
                               (not cur or marker.get("gen", -1) >= cur.get("gen", -1)))
            # dirty=True means "the world on this laptop is newer than Drive"
            config.save_session({"nonce": self.model["nonce"], "gen": self.model["gen"], "dirty": local_newer})
            nw = prev.get("new_world")
            self._new_world = None
            if local_newer and os.path.isdir(os.path.join(config.SERVER_DIR, "world")):
                self.log("Last session on this laptop didn't finish saving. Using the newer local world.", "warn")
                if nw:
                    self.log("The requested NEW WORLD will be created next time, after this one is saved.", "warn")
                self.unsaved = True
            elif nw:
                if cur:
                    self._download_world(cur)   # keeps ops, whitelist and bans from the old world
                shutil.rmtree(os.path.join(config.SERVER_DIR, "world"), ignore_errors=True)
                self._new_world = nw
                seed = nw.get("seed") or "random"
                self.log(f"Creating a NEW WORLD (seed: {seed}, type: {nw.get('level_type', 'normal')}). "
                         "The old world stays safe in Drive until this one is saved.", "warn")
            elif cur:
                self._download_world(cur)
            else:
                for item in config.WORLD_ITEMS:
                    p = os.path.join(config.SERVER_DIR, item)
                    if os.path.isdir(p):
                        shutil.rmtree(p)
                self.log("No world in Drive yet. A brand new world will be generated.")
            mcserver.prepare_files(online, data.get("world_settings"), self._new_world)

            self.server = mcserver.Server(java, settings["ram_gb"], lambda l: self.emit("console", {"line": l}))
            self.server.start()
            self._stop_evt.clear()
            threading.Thread(target=self._heartbeat, daemon=True).start()
            self.log("Server is booting...")
            self.server.wait_ready()
            for cmd in mcserver.live_commands(data.get("world_settings")):
                self.server.send(cmd)
            line = self.server.send_and_wait("seed", r"Seed: \[-?\d+\]", timeout=15)
            m = re.search(r"Seed: \[(-?\d+)\]", line or "")
            if m:
                self.model["world_info"] = {"seed": m.group(1)}
            self.unsaved = True
            config.save_session({"nonce": self.model["nonce"], "gen": self.model["gen"], "dirty": True})
            self.set_phase("running")
            self.log("Server is up. Friends can join now.", "ok")
            threading.Thread(target=self._autosave_loop, args=(settings["autosave_min"],), daemon=True).start()
            threading.Thread(target=self._watch_process, daemon=True).start()
        except Busy:
            self.set_phase("idle")
            raise
        except Exception as e:
            self.log(f"Start failed: {e}", "error")
            self._log_trace()
            if self.server and self.server.running():
                self.server.stop()
            if self.model and not self.unsaved:
                self._release()
            self.set_phase("idle")
            raise

    def _autosave_loop(self, minutes):
        while not self._stop_evt.wait(max(5, minutes) * 60):
            if self.phase != "running" or self.fenced:
                continue
            self.save_now("auto")

    def save_now(self, label="manual"):
        if self.phase != "running" or self._upload_lock.locked():
            return False
        snap = os.path.join(config.TMP_DIR, "snapshot")
        try:
            self.server.snapshot(snap)          # game keeps running; we work on the copy
            return self._upload_world(snap, label)
        except Exception as e:
            self.log(f"Mid-session save failed (game unaffected): {e}", "warn")
            return False
        finally:
            shutil.rmtree(snap, ignore_errors=True)

    def _watch_process(self):
        while self.server and self.server.running():
            time.sleep(2)
        if self.phase == "running":   # crashed or someone typed 'stop' in console
            self.log("Server process ended. Saving world...", "warn")
            threading.Thread(target=self.stop, daemon=True).start()

    # ---------------- stop ----------------
    def stop(self):
        if self.phase in ("stopping", "idle"):
            return
        self.set_phase("stopping")
        try:
            if self.server and self.server.running():
                self.log("Stopping server (saving the game)...")
                self.server.stop()
            snap = os.path.join(config.TMP_DIR, "final")
            mcserver.copy_world_items(config.SERVER_DIR, snap)
            ok = self._upload_world(snap, "final")
            shutil.rmtree(snap, ignore_errors=True)
            if ok:
                self._release()
                self.unsaved = False
                config.save_session(None)
                self.log("All saved. Server released for others.", "ok")
            elif self.fenced:
                self._stop_evt.set()
                self.model = None
                config.save_session(None)
                self.unsaved = False
            self.set_phase("idle")
        except Exception as e:
            self.log(f"Saving to Drive failed: {e}. Your world is safe on this laptop. "
                     "Press RETRY SAVE when you have internet.", "error")
            self._log_trace()
            self.set_phase("saving")   # heartbeat keeps the lock alive while we retry

    def retry_save(self):
        self.phase = "running-off"
        self.stop()

    def _release(self):
        self._stop_evt.set()
        try:
            with self._state_lock:
                ok, _ = self._still_mine()
                if ok:
                    self.model.update(state="idle", players=[], ip=[])
                    self.drive.write_json(self.group["state_id"], self.model)
        finally:
            self.model = None
            self.server = None

    def _log_trace(self):
        try:
            with open(config.LOG_FILE, "a", encoding="utf-8") as f:
                f.write(traceback.format_exc() + "\n")
        except OSError:
            pass

    # ---------------- maintenance (group-wide, only while nobody hosts) ----------------
    def _edit_idle(self, what, change):
        """Read state.json, refuse if someone is hosting, apply change(data), write back.
        Hosts rewrite state.json every minute, so edits during a session would be lost."""
        data, mtime, now = self.read_state()
        if data.get("state") == "running" and now - mtime < config.STALE_SEC:
            raise RuntimeError(f"Someone is hosting right now. {what} only while the server is idle.")
        change(data)
        self.drive.write_json(self.group["state_id"], data)
        return data

    def restore_previous(self):
        """Swap current and previous save."""
        def change(data):
            w = data.get("world") or {}
            if not w.get("previous"):
                raise RuntimeError("There is no previous save to restore.")
            data["world"] = {"current": w["previous"], "previous": w.get("current"), "archive": w.get("archive")}
        self._edit_idle("Restore", change)
        self.log("Restored the previous save. It will be used next time someone hosts.", "ok")

    def restore_archive(self):
        """Bring back the world from before the last NEW WORLD (swaps it with the current world)."""
        def change(data):
            w = data.get("world") or {}
            if not w.get("archive"):
                raise RuntimeError("There is no old world to bring back.")
            data["world"] = {"current": w["archive"], "previous": w.get("previous"), "archive": w.get("current")}
            data.pop("world_info", None)   # seed belongs to the other world now
            data.pop("new_world", None)
        self._edit_idle("Switching worlds", change)
        self.log("Old world restored. The newer world is kept as the archive, so you can switch back.", "ok")

    def set_version(self, version):
        mcserver.version_info(version)  # validates
        self._edit_idle("Change the version", lambda d: d.__setitem__("mc_version", version))
        self.log(f"Group switched to Minecraft {version}. Everyone must play {version}.", "ok")

    def set_world_settings(self, raw):
        clean = mcserver.clean_world_settings(raw)
        self._edit_idle("Change world settings", lambda d: d.__setitem__("world_settings", clean))
        self.log("World settings saved. They apply the next time anyone hosts.", "ok")
        return clean

    def request_new_world(self, seed, level_type):
        if level_type not in mcserver.LEVEL_TYPES:
            raise RuntimeError("Unknown world type.")
        nw = {"seed": " ".join(str(seed or "").split())[:64], "level_type": level_type, "requested_by": self.me}
        self._edit_idle("Start a new world", lambda d: d.__setitem__("new_world", nw))
        self.log(f"NEW WORLD queued (seed: {nw['seed'] or 'random'}, type: {level_type}). "
                 "It is generated the next time anyone hosts.", "warn")

    def cancel_new_world(self):
        self._edit_idle("Cancel", lambda d: d.pop("new_world", None))
        self.log("New world cancelled. The current world stays.", "ok")
