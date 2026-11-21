"""Minimal Google Drive client (REST + requests).

Scope is drive.file: the app can only see files it created itself, never the rest
of the owner's Drive. Time comparisons use Google's own clock (HTTP Date header vs
file modifiedTime), so laptops with wrong clocks can't fool the heartbeat check.
"""
import base64
import hashlib
import http.server
import json
import os
import secrets
import threading
import time
import urllib.parse
import webbrowser
from email.utils import parsedate_to_datetime
from datetime import datetime

import requests

SCOPE = "https://www.googleapis.com/auth/drive.file"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3"
FOLDER_MIME = "application/vnd.google-apps.folder"


class DriveError(Exception):
    pass


# ---------------- one-time owner login (loopback + PKCE) ----------------

def login(client_id: str, client_secret: str, timeout: int = 300) -> str:
    """Opens the browser for Google sign-in and returns a refresh token."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    result = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if q.get("state", [""])[0] == state:
                result["code"] = q.get("code", [None])[0]
                result["error"] = q.get("error", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<h2 style='font-family:monospace'>Done. You can close this tab and go back to BlockHost.</h2>".encode())

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    redirect = f"http://127.0.0.1:{srv.server_port}"
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": redirect, "response_type": "code",
        "scope": SCOPE, "access_type": "offline", "prompt": "consent",
        "code_challenge": challenge, "code_challenge_method": "S256", "state": state,
    })
    webbrowser.open(url)
    srv.timeout = 2
    deadline = time.time() + timeout
    while "code" not in result and "error" not in result and time.time() < deadline:
        srv.handle_request()
    srv.server_close()
    if not result.get("code"):
        raise DriveError(f"Google sign-in did not finish ({result.get('error') or 'timed out'}).")
    r = requests.post(TOKEN_URL, data={
        "code": result["code"], "client_id": client_id, "client_secret": client_secret,
        "redirect_uri": redirect, "grant_type": "authorization_code", "code_verifier": verifier,
    }, timeout=30)
    if r.status_code != 200 or "refresh_token" not in r.json():
        raise DriveError(f"Google refused the sign-in: {r.text[:300]}")
    return r.json()["refresh_token"]


# ---------------- API client ----------------

class Drive:
    def __init__(self, client_id, client_secret, refresh_token):
        self.cid, self.secret, self.refresh = client_id, client_secret, refresh_token
        self._token, self._expires = None, 0
        self._lock = threading.Lock()
        self.s = requests.Session()

    def _auth(self):
        with self._lock:
            if self._token and time.time() < self._expires - 60:
                return self._token
            r = requests.post(TOKEN_URL, data={
                "client_id": self.cid, "client_secret": self.secret,
                "refresh_token": self.refresh, "grant_type": "refresh_token",
            }, timeout=30)
            if r.status_code != 200:
                raise DriveError("Google login expired or was revoked. The owner must run setup again.")
            j = r.json()
            self._token, self._expires = j["access_token"], time.time() + j.get("expires_in", 3600)
            return self._token

    def _req(self, method, url, retries=4, **kw):
        base_headers = kw.pop("headers", None) or {}
        timeout = kw.pop("timeout", 60)
        for attempt in range(retries):
            headers = dict(base_headers, Authorization="Bearer " + self._auth())
            try:
                r = self.s.request(method, url, headers=headers, timeout=timeout, **kw)
            except requests.RequestException as e:
                if attempt == retries - 1:
                    raise DriveError(f"No connection to Google Drive: {e}")
                time.sleep(2 ** attempt)
                continue
            if r.status_code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            if r.status_code >= 400:
                raise DriveError(f"Drive error {r.status_code}: {r.text[:300]}")
            return r
        raise DriveError("Drive request failed.")

    @staticmethod
    def google_now(resp) -> float:
        return parsedate_to_datetime(resp.headers["Date"]).timestamp()

    def now(self) -> float:
        """Current time on Google's clock."""
        return self.google_now(self._req("GET", f"{API}/about", params={"fields": "kind"}))

    @staticmethod
    def parse_time(rfc3339: str) -> float:
        return datetime.fromisoformat(rfc3339.replace("Z", "+00:00")).timestamp()

    # --- folders / files ---
    def create_folder(self, name):
        r = self._req("POST", f"{API}/files", json={"name": name, "mimeType": FOLDER_MIME}, params={"fields": "id"})
        return r.json()["id"]

    def create_json(self, folder_id, name, data):
        meta = {"name": name, "parents": [folder_id], "mimeType": "application/json"}
        body = (
            "--bh\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n" + json.dumps(meta) +
            "\r\n--bh\r\nContent-Type: application/json\r\n\r\n" + json.dumps(data) + "\r\n--bh--"
        )
        r = self._req("POST", f"{UPLOAD}/files", params={"uploadType": "multipart", "fields": "id"},
                      data=body.encode(), headers={"Content-Type": "multipart/related; boundary=bh"})
        return r.json()["id"]

    def read_json(self, file_id):
        """Returns (data, modified_ts, google_now_ts)."""
        meta = self._req("GET", f"{API}/files/{file_id}", params={"fields": "modifiedTime"})
        r = self._req("GET", f"{API}/files/{file_id}", params={"alt": "media"})
        return r.json(), self.parse_time(meta.json()["modifiedTime"]), self.google_now(r)

    def write_json(self, file_id, data):
        self._req("PATCH", f"{UPLOAD}/files/{file_id}", params={"uploadType": "media"},
                  data=json.dumps(data).encode(), headers={"Content-Type": "application/json"})

    def delete(self, file_id):
        try:
            self._req("DELETE", f"{API}/files/{file_id}", retries=2)
        except DriveError:
            pass  # an orphaned old file only wastes a little space

    # --- big files (resumable upload, streamed download) ---
    def upload_file(self, folder_id, name, path, progress=None):
        size = os.path.getsize(path)
        r = self._req("POST", f"{UPLOAD}/files", params={"uploadType": "resumable", "fields": "id"},
                      json={"name": name, "parents": [folder_id]},
                      headers={"X-Upload-Content-Type": "application/octet-stream",
                               "X-Upload-Content-Length": str(size)})
        session = r.headers["Location"]
        chunk, sent = 8 * 1024 * 1024, 0
        with open(path, "rb") as f:
            while True:
                f.seek(sent)
                data = f.read(chunk)
                end = sent + len(data) - 1
                hdr = {"Content-Range": f"bytes {sent}-{end}/{size}" if data else f"bytes */{size}"}
                for attempt in range(5):
                    try:
                        resp = self.s.put(session, data=data, headers=hdr, timeout=300)
                        break
                    except requests.RequestException:
                        if attempt == 4:
                            raise DriveError("Upload lost its connection.")
                        time.sleep(2 ** attempt)
                if resp.status_code in (200, 201):
                    if progress:
                        progress(size, size)
                    return resp.json()["id"]
                if resp.status_code == 308:
                    rng = resp.headers.get("Range")
                    sent = int(rng.split("-")[1]) + 1 if rng else 0
                    if progress:
                        progress(sent, size)
                    continue
                raise DriveError(f"Upload failed ({resp.status_code}): {resp.text[:200]}")

    def download_file(self, file_id, path, progress=None):
        r = self._req("GET", f"{API}/files/{file_id}", params={"alt": "media"}, stream=True, timeout=300)
        total, got = int(r.headers.get("Content-Length", 0)), 0
        with open(path, "wb") as f:
            for block in r.iter_content(1024 * 1024):
                f.write(block)
                got += len(block)
                if progress and total:
                    progress(got, total)
