"""Running the Minecraft server process: Java lookup, server.jar download, console I/O."""
import hashlib
import os
import re
import shutil
import socket
import subprocess
import threading
import time

import requests

from . import config, system

MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
NO_WINDOW = system.NO_WINDOW


# ---------------- Java ----------------

def _java_major(exe):
    try:
        out = subprocess.run([exe, "-version"], capture_output=True, text=True, timeout=20,
                             creationflags=NO_WINDOW).stderr
        m = re.search(r'version "(\d+)', out)
        return int(m.group(1)) if m else 0
    except (OSError, subprocess.SubprocessError):
        return 0


def find_java(min_major):
    cands = system.java_candidates()
    for c in cands:
        if os.path.isfile(c) and _java_major(c) >= min_major:
            return c
    return None


def install_java(major):
    return system.install_java(major)


# ---------------- server.jar ----------------

def version_info(version):
    m = requests.get(MANIFEST, timeout=30).json()
    v = next((x for x in m["versions"] if x["id"] == version), None)
    if not v:
        raise RuntimeError(f"Minecraft version {version} not found at Mojang.")
    d = requests.get(v["url"], timeout=30).json()
    return d["downloads"]["server"], d.get("javaVersion", {}).get("majorVersion", 21)


def _sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def ensure_jar(version, log):
    """Downloads the official server.jar if missing or wrong version. Returns java major needed."""
    jar = os.path.join(config.SERVER_DIR, "server.jar")
    stamp = os.path.join(config.SERVER_DIR, "server.version")
    server, java_major = version_info(version)
    have = open(stamp).read().strip() if os.path.exists(stamp) else ""
    if have == version and os.path.exists(jar) and _sha1(jar) == server["sha1"]:
        return java_major
    log(f"Downloading official Minecraft {version} server...")
    tmp = jar + ".part"
    with requests.get(server["url"], stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for b in r.iter_content(1 << 20):
                f.write(b)
    if _sha1(tmp) != server["sha1"]:
        os.remove(tmp)
        raise RuntimeError("Downloaded server.jar failed its checksum. Try again.")
    os.replace(tmp, jar)
    with open(stamp, "w") as f:
        f.write(version)
    return java_major


def prepare_files(motd, online_mode):
    with open(os.path.join(config.SERVER_DIR, "eula.txt"), "w") as f:
        f.write("eula=true\n")
    props = os.path.join(config.SERVER_DIR, "server.properties")
    flag = "true" if online_mode else "false"
    want = {"online-mode": flag, "enforce-secure-profile": flag}
    lines = open(props, encoding="utf-8").read().splitlines() if os.path.exists(props) else [f"motd={motd}"]
    keys = set()
    for i, line in enumerate(lines):
        k = line.split("=", 1)[0]
        if k in want:
            lines[i] = f"{k}={want[k]}"
        keys.add(k)
    lines += [f"{k}={v}" for k, v in want.items() if k not in keys]
    with open(props, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def server_port():
    props = os.path.join(config.SERVER_DIR, "server.properties")
    try:
        for line in open(props, encoding="utf-8"):
            if line.startswith("server-port="):
                return int(line.split("=", 1)[1])
    except (OSError, ValueError):
        pass
    return 25565


# ---------------- network / firewall ----------------

def lan_ips():
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))  # no packet is sent; just picks the LAN interface
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return [ip for ip in ips if not ip.startswith("127.")]


def firewall_rule_exists(port):
    return system.firewall_rule_exists(port)


def add_firewall_rule(port):
    return system.add_firewall_rule(port)


# ---------------- the process ----------------

FATAL = [
    ("World files may be corrupted", "The world save is damaged. Open OPTIONS > RESTORE PREVIOUS SAVE, then start again."),
    ("FAILED TO BIND TO PORT", "Port is already in use. Is another Minecraft server running on this laptop?"),
    ("Failed to start the minecraft server", "Minecraft failed to start. Check the console for details."),
    ("Could not reserve enough space", "Not enough memory. Lower the RAM in OPTIONS."),
    ("UnsupportedClassVersionError", "Wrong Java version for this Minecraft version."),
]


class Server:
    def __init__(self, java, ram_gb, on_line):
        self.java, self.ram, self.on_line = java, ram_gb, on_line
        self.proc = None
        self.ready = threading.Event()
        self.failure = None  # set to a human message if startup fails
        self.players = set()
        self._waiters = []  # (regex, event)
        self._wlock = threading.Lock()

    def start(self):
        self.proc = subprocess.Popen(
            [self.java, f"-Xms1G", f"-Xmx{self.ram}G", "-jar", "server.jar", "nogui"],
            cwd=config.SERVER_DIR, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
            bufsize=1, creationflags=NO_WINDOW)
        self._job = system.tie_child_to_parent(self.proc)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            line = line.rstrip()
            if "Done (" in line and "For help" in line:
                self.ready.set()
            if not self.ready.is_set() and self.failure is None:
                for needle, msg in FATAL:
                    if needle in line:
                        self.failure = msg
            m = re.search(r"\]: (\w+) joined the game", line)
            if m:
                self.players.add(m.group(1))
            m = re.search(r"\]: (\w+) left the game", line)
            if m:
                self.players.discard(m.group(1))
            with self._wlock:
                for rx, ev in self._waiters:
                    if re.search(rx, line):
                        ev.set()
            self.on_line(line)

    def wait_ready(self, timeout=600):
        """True when the server finished booting; raises on a detected failure."""
        end = time.time() + timeout
        while time.time() < end:
            if self.ready.is_set():
                return True
            if self.failure or not self.running():
                time.sleep(1)  # let the last lines reach the console
                if self.running():
                    self.proc.kill()
                raise RuntimeError(self.failure or "The server process exited during startup. Check the console.")
            time.sleep(0.5)
        raise RuntimeError("Server took over 10 minutes to start. Check the console.")

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def send(self, cmd):
        if self.running():
            try:
                self.proc.stdin.write(cmd + "\n")
                self.proc.stdin.flush()
            except OSError:
                pass

    def send_and_wait(self, cmd, regex, timeout=60):
        ev = threading.Event()
        with self._wlock:
            self._waiters.append((regex, ev))
        try:
            self.send(cmd)
            return ev.wait(timeout)
        finally:
            with self._wlock:
                self._waiters.remove((regex, ev))

    def stop(self, timeout=180):
        if not self.running():
            return True
        self.send("stop")
        try:
            self.proc.wait(timeout)
            return True
        except subprocess.TimeoutExpired:
            self.proc.kill()
            return False

    def snapshot(self, dest):
        """Consistent copy of the world while the game keeps running.
        Saving is paused only for the copy itself (usually under a second or two)."""
        self.send("save-off")
        try:
            self.send_and_wait("save-all flush", r"Saved the game", timeout=120)
            copy_world_items(config.SERVER_DIR, dest)
        finally:
            self.send("save-on")


def copy_world_items(src_dir, dest):
    if os.path.exists(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)
    for item in config.WORLD_ITEMS:
        s = os.path.join(src_dir, item)
        if os.path.isdir(s):
            shutil.copytree(s, os.path.join(dest, item), ignore=shutil.ignore_patterns("session.lock"))
        elif os.path.isfile(s):
            shutil.copy2(s, dest)
