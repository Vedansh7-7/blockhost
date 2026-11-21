"""Everything that differs between Windows and macOS lives here."""
import ctypes
import glob
import os
import shutil
import subprocess
import sys
import webbrowser

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
NO_WINDOW = 0x08000000 if IS_WIN else 0   # hide console windows of child processes on Windows


def data_home():
    if os.environ.get("BLOCKHOST_HOME"):           # used by tests
        return os.environ["BLOCKHOST_HOME"]
    if IS_WIN:
        return os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "BlockHost")
    if IS_MAC:
        return os.path.expanduser("~/Library/Application Support/BlockHost")
    return os.path.expanduser("~/.local/share/blockhost")


def webview_gui():
    return "edgechromium" if IS_WIN else None


def open_folder(path):
    if IS_WIN:
        subprocess.Popen(["explorer", path])
    elif IS_MAC:
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


# ======================= protecting the local settings cache =======================
# Windows: DPAPI (tied to the Windows user).  macOS: a random key kept in the login Keychain.

def protect_local(data: bytes) -> bytes:
    if IS_WIN:
        return _dpapi(data, True)
    nonce = os.urandom(12)
    return b"BHL1" + nonce + AESGCM(_local_key()).encrypt(nonce, data, None)


def unprotect_local(blob: bytes) -> bytes:
    if IS_WIN:
        return _dpapi(blob, False)
    if not blob.startswith(b"BHL1"):
        raise ValueError("unknown cache format")
    return AESGCM(_local_key()).decrypt(blob[4:16], blob[16:], None)


_KC_SERVICE, _KC_ACCOUNT = "BlockHost", "local-cache-key"


def _local_key() -> bytes:
    """32-byte key from the macOS Keychain (created on first use).
    Falls back to a private file if the Keychain is unavailable."""
    if IS_MAC:
        r = subprocess.run(["security", "find-generic-password", "-s", _KC_SERVICE, "-a", _KC_ACCOUNT, "-w"],
                           capture_output=True, text=True)
        if r.returncode == 0 and len(r.stdout.strip()) == 64:
            return bytes.fromhex(r.stdout.strip())
        key = os.urandom(32)
        w = subprocess.run(["security", "add-generic-password", "-U", "-s", _KC_SERVICE, "-a", _KC_ACCOUNT,
                            "-w", key.hex()], capture_output=True, text=True)
        if w.returncode == 0:
            return key
    path = os.path.join(data_home(), ".localkey")
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()
    os.makedirs(data_home(), exist_ok=True)
    key = os.urandom(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    return key


if IS_WIN:
    import ctypes.wintypes as wt

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data: bytes, protect: bool) -> bytes:
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(data, len(data))
    src, out = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), _Blob()
    if protect:
        ok = crypt32.CryptProtectData(ctypes.byref(src), "BlockHost", None, None, None, 0, ctypes.byref(out))
    else:
        ok = crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out))
    if not ok:
        raise OSError("Windows could not unlock the saved settings.")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


# ======================= Java =======================

def java_candidates():
    c = []
    if os.environ.get("JAVA_HOME"):
        c.append(os.path.join(os.environ["JAVA_HOME"], "bin", "java.exe" if IS_WIN else "java"))
    if IS_WIN:
        for root in (r"C:\Program Files\Eclipse Adoptium", r"C:\Program Files\Java",
                     r"C:\Program Files\Microsoft", r"C:\Program Files\Zulu"):
            c += sorted(glob.glob(os.path.join(root, "*", "bin", "java.exe")), reverse=True)
        w = shutil.which("java")
        if w:
            c.append(w)
    elif IS_MAC:
        # Never call /usr/bin/java on a Mac without Java: it pops up an install dialog.
        c += sorted(glob.glob("/Library/Java/JavaVirtualMachines/*/Contents/Home/bin/java"), reverse=True)
        c += sorted(glob.glob(os.path.expanduser("~/Library/Java/JavaVirtualMachines/*/Contents/Home/bin/java")), reverse=True)
        c += sorted(glob.glob("/opt/homebrew/opt/openjdk*/bin/java") + glob.glob("/usr/local/opt/openjdk*/bin/java"), reverse=True)
    else:
        w = shutil.which("java")
        if w:
            c.append(w)
    return c


def install_java(major):
    """Returns (ok, message)."""
    try:
        if IS_WIN:
            p = subprocess.run(["winget", "install", "--id", f"EclipseAdoptium.Temurin.{major}.JRE", "-e", "--silent",
                                "--accept-package-agreements", "--accept-source-agreements"],
                               capture_output=True, text=True, timeout=900, creationflags=NO_WINDOW)
            return p.returncode == 0, (p.stdout or p.stderr)[-300:]
        if IS_MAC:
            brew = shutil.which("brew") or next((b for b in ("/opt/homebrew/bin/brew", "/usr/local/bin/brew")
                                                 if os.path.exists(b)), None)
            if brew:
                p = subprocess.run([brew, "install", "--cask", f"temurin@{major}"],
                                   capture_output=True, text=True, timeout=1800)
                if p.returncode == 0:
                    return True, "installed with Homebrew"
            webbrowser.open(f"https://adoptium.net/temurin/releases/?os=mac&package=jdk&version={major}")
            return False, (f"Your browser opened the Java {major} download page. Download the .pkg for your Mac, "
                           "install it, then press START again.")
        return False, f"Install Java {major} with your package manager."
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)


# ======================= firewall =======================

def firewall_rule_exists(port):
    if not IS_WIN:
        return True  # macOS asks "allow incoming connections?" by itself the first time
    p = subprocess.run(["netsh", "advfirewall", "firewall", "show", "rule", f"name=BlockHost {port}"],
                       capture_output=True, text=True, creationflags=NO_WINDOW)
    return p.returncode == 0 and "BlockHost" in p.stdout


def add_firewall_rule(port):
    """Windows: one UAC prompt; private networks only, never public Wi-Fi."""
    if not IS_WIN:
        return True
    args = (f'advfirewall firewall add rule name="BlockHost {port}" dir=in action=allow '
            f'protocol=TCP localport={port} profile=private,domain')
    return ctypes.windll.shell32.ShellExecuteW(None, "runas", "netsh", args, None, 0) > 32


# ======================= server dies with the launcher =======================

def tie_child_to_parent(proc):
    """If the launcher crashes, the Minecraft server must not keep running unsupervised."""
    if IS_WIN:
        return _win_job(proc.pid)
    # POSIX: a tiny watchdog shell kills the server once the launcher's PID is gone.
    # It also exits on its own as soon as the server ends normally.
    p, c = os.getpid(), proc.pid
    watchdog = (f"while kill -0 {p} 2>/dev/null; do kill -0 {c} 2>/dev/null || exit 0; sleep 3; done; "
                f"kill -TERM {c} 2>/dev/null; sleep 20; kill -KILL {c} 2>/dev/null")
    try:
        return subprocess.Popen(["/bin/sh", "-c", watchdog], start_new_session=True,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return None


def _win_job(pid):
    try:
        k32 = ctypes.windll.kernel32
        vp = ctypes.c_void_p
        k32.CreateJobObjectW.restype = vp
        k32.OpenProcess.restype = vp
        k32.SetInformationJobObject.argtypes = [vp, ctypes.c_int, vp, ctypes.c_uint32]
        k32.AssignProcessToJobObject.argtypes = [vp, vp]
        k32.CloseHandle.argtypes = [vp]
        job = k32.CreateJobObjectW(None, None)

        class LIMIT(ctypes.Structure):
            _fields_ = [("a", ctypes.c_int64), ("b", ctypes.c_int64), ("flags", ctypes.c_uint32),
                        ("c", ctypes.c_size_t), ("d", ctypes.c_size_t), ("e", ctypes.c_uint32),
                        ("f", ctypes.c_size_t), ("g", ctypes.c_uint32), ("h", ctypes.c_uint32)]

        class EXT(ctypes.Structure):
            _fields_ = [("basic", LIMIT), ("io", ctypes.c_uint64 * 6), ("p", ctypes.c_size_t * 4)]

        info = EXT()
        info.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
        h = k32.OpenProcess(0x1F0FFF, False, pid)
        k32.AssignProcessToJobObject(job, h)
        k32.CloseHandle(h)
        return job
    except Exception:
        return None
