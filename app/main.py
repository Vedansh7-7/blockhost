"""BlockHost launcher: window + bridge between the UI and the hosting session."""
import base64
import json
import os
import subprocess
import sys
import threading
import webbrowser

import webview

from core import config, crypto, mcserver, system
from core.drive import Drive, DriveError, login
from core.session import Busy, Session

DEFAULT_VERSION = "26.3"


def resource(*parts):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


class Api:
    def __init__(self):
        self._window = None
        self._group = config.cached_group()
        self._session = None
        self.allow_close = False
        if self._group:
            self._session = Session(self._group, self.emit)

    # ---------- plumbing ----------
    def emit(self, kind, data=None):
        if self._window:
            try:
                self._window.evaluate_js(f"window.onEvent && onEvent({json.dumps(kind)}, {json.dumps(data or {})})")
            except Exception:
                pass

    def _bg(self, fn, *a):
        def run():
            try:
                fn(*a)
            except Busy as b:
                self.emit("host_busy", b.info)
            except Exception as e:
                self.emit("error", {"text": str(e)})
        threading.Thread(target=run, daemon=True).start()

    # ---------- boot / setup ----------
    def boot(self):
        return {"has_group": bool(self._group), "group_name": (self._group or {}).get("group_name"),
                "settings": config.load_settings()}

    def pick_group_file(self):
        r = self._window.create_file_dialog(webview.OPEN_DIALOG, file_types=("BlockHost group (*.bhgroup)", "All files (*.*)"))
        return r[0] if r else None

    def join_group(self, path, password):
        try:
            with open(path, "rb") as f:
                group = crypto.open_group(f.read(), password)
            Drive(group["client_id"], group["client_secret"], group["refresh_token"]).read_json(group["state_id"])
        except crypto.BadPassword:
            return {"ok": False, "error": "Wrong password."}
        except (OSError, crypto.CorruptArchive, DriveError, KeyError) as e:
            return {"ok": False, "error": str(e)}
        self._set_group(group)
        return {"ok": True}

    def create_group(self, client_id, client_secret, group_name, password, version, online_mode=True):
        if len(password) < 8:
            return {"ok": False, "error": "Use a password of at least 8 characters."}
        try:
            mcserver.version_info(version)
            refresh = login(client_id.strip(), client_secret.strip())
            d = Drive(client_id.strip(), client_secret.strip(), refresh)
            folder = d.create_folder(f"BlockHost - {group_name}")
            state_id = d.create_json(folder, "state.json", {
                "v": 1, "state": "idle", "gen": 0, "mc_version": version, "online_mode": bool(online_mode),
                "world_settings": mcserver.clean_world_settings({"motd": f"{group_name} world"}),
                "world": {"current": None, "previous": None}})
        except Exception as e:
            return {"ok": False, "error": str(e)}
        group = {"group_name": group_name, "client_id": client_id.strip(), "client_secret": client_secret.strip(),
                 "refresh_token": refresh, "folder_id": folder, "state_id": state_id,
                 "world_key": base64.b64encode(os.urandom(32)).decode(), "mc_version": version}
        blob = crypto.seal_group(group, password)
        r = self._window.create_file_dialog(webview.SAVE_DIALOG, save_filename=f"{group_name}.bhgroup")
        path = r if isinstance(r, str) else (r[0] if r else None)
        if not path:
            path = os.path.join(os.path.expanduser("~"), "Desktop", f"{group_name}.bhgroup")
        with open(path, "wb") as f:
            f.write(blob)
        self._set_group(group)
        return {"ok": True, "path": path}

    def _set_group(self, group):
        config.cache_group(group)
        self._group = group
        self._session = Session(group, self.emit)

    def leave_group(self):
        if self._session and self._session.phase != "idle":
            return {"ok": False, "error": "Stop the server first."}
        config.forget_group()
        self._group, self._session = None, None
        return {"ok": True}

    # ---------- hosting ----------
    def status(self):
        try:
            s = self._session.status()
            s["phase"] = self._session.phase
            s["fenced"] = self._session.fenced
            return s
        except Exception as e:
            return {"error": str(e), "phase": self._session.phase if self._session else "idle"}

    def start(self, takeover=False):
        self._bg(self._session.start, config.load_settings(), bool(takeover))

    def stop(self):
        self._bg(self._session.stop)

    def save_now(self):
        self._bg(self._session.save_now, "manual")

    def retry_save(self):
        self._bg(self._session.retry_save)

    def command(self, text):
        if self._session and self._session.server:
            self._session.server.send(text.strip().lstrip("/"))

    def install_java(self, major):
        def run():
            self.emit("log", {"text": f"Installing Java {major} (this can take a few minutes)...", "level": "info"})
            ok, msg = mcserver.install_java(major)
            self.emit("log", {"text": "Java installed. Press START again." if ok else f"Java install failed: {msg}",
                              "level": "ok" if ok else "error"})
        threading.Thread(target=run, daemon=True).start()

    def restore_previous(self):
        try:
            self._session.restore_previous()
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def set_version(self, v):
        return self._try(self._session.set_version, v.strip())

    # ---------- world screen ----------
    def set_world_settings(self, s):
        return self._try(self._session.set_world_settings, s)

    def request_new_world(self, seed, level_type):
        return self._try(self._session.request_new_world, seed, level_type)

    def cancel_new_world(self):
        return self._try(self._session.cancel_new_world)

    def restore_archive(self):
        return self._try(self._session.restore_archive)

    def _try(self, fn, *a):
        try:
            return {"ok": True, "result": fn(*a)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def open_url(self, url):
        if url.startswith("https://"):
            webbrowser.open(url)

    def accept_eula(self):
        s = config.load_settings()
        s["eula"] = True
        config.save_settings(s)
        return True

    def save_settings(self, s):
        cur = config.load_settings()
        cur["ram_gb"] = max(1, min(16, int(s.get("ram_gb", cur["ram_gb"]))))
        cur["autosave_min"] = max(5, min(120, int(s.get("autosave_min", cur["autosave_min"]))))
        config.save_settings(cur)
        return cur

    def open_folder(self):
        system.open_folder(config.HOME)

    # ---------- closing ----------
    def quit(self):
        """QUIT button: safe close (stop + save first if hosting)."""
        if self._session and self._session.phase != "idle":
            self.emit("confirm_close", {"phase": self._session.phase})
        else:
            self.allow_close = True
            self._window.destroy()

    def stop_and_quit(self):
        def run():
            self._session.stop()
            if self._session.phase == "idle":
                self.allow_close = True
                self._window.destroy()
        threading.Thread(target=run, daemon=True).start()

    def force_quit(self):
        self.allow_close = True
        self._window.destroy()


def main():
    config.ensure_dirs()
    api = Api()

    def on_closing():
        if api.allow_close or not api._session or api._session.phase == "idle":
            return True
        api.emit("confirm_close", {"phase": api._session.phase})
        return False  # cancel the X button; the UI asks what to do

    win = webview.create_window("BlockHost", resource("ui", "index.html"), js_api=api,
                                width=980, height=680, min_size=(820, 580), background_color="#1b140e")
    api._window = win
    win.events.closing += on_closing
    webview.start(gui=system.webview_gui(), debug=False)


if __name__ == "__main__":
    main()
