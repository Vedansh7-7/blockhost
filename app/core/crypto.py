"""Encryption helpers.

- Group file: the shared settings (Google login, world key) sealed with the group password.
- World archives: streamed AES-256-GCM in 1 MiB chunks, so big worlds never sit in RAM.
- Local cache protection lives in system.py (DPAPI on Windows, Keychain on macOS).
"""
import json
import os
import struct

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from cryptography.exceptions import InvalidTag

GROUP_MAGIC = b"BHG1"
WORLD_MAGIC = b"BHW1"
CHUNK = 1024 * 1024


class BadPassword(Exception):
    pass


class CorruptArchive(Exception):
    pass


# ---------------- group file (password based) ----------------

def _derive(password: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(password.encode("utf-8"))


def seal_group(data: dict, password: str) -> bytes:
    salt, nonce = os.urandom(16), os.urandom(12)
    ct = AESGCM(_derive(password, salt)).encrypt(nonce, json.dumps(data).encode(), GROUP_MAGIC)
    return GROUP_MAGIC + salt + nonce + ct


def open_group(blob: bytes, password: str) -> dict:
    if not blob.startswith(GROUP_MAGIC):
        raise CorruptArchive("This is not a group file.")
    salt, nonce, ct = blob[4:20], blob[20:32], blob[32:]
    try:
        return json.loads(AESGCM(_derive(password, salt)).decrypt(nonce, ct, GROUP_MAGIC))
    except InvalidTag:
        raise BadPassword("Wrong password.")


# ---------------- world archives (key based, streamed) ----------------

def encrypt_file(src: str, dst: str, key: bytes) -> None:
    aes, prefix = AESGCM(key), os.urandom(8)
    size = os.path.getsize(src)
    with open(src, "rb") as fi, open(dst, "wb") as fo:
        fo.write(WORLD_MAGIC + prefix)
        counter, done = 0, 0
        while True:
            block = fi.read(CHUNK)
            done += len(block)
            last = done >= size
            nonce = prefix + struct.pack(">I", counter)
            ct = aes.encrypt(nonce, block, b"\x01" if last else b"\x00")
            fo.write(struct.pack(">I", len(ct)) + ct)
            counter += 1
            if last:
                break


def decrypt_file(src: str, dst: str, key: bytes) -> None:
    aes = AESGCM(key)
    with open(src, "rb") as fi, open(dst, "wb") as fo:
        head = fi.read(12)
        if head[:4] != WORLD_MAGIC:
            raise CorruptArchive("World file is not a BlockHost archive.")
        prefix, counter = head[4:], 0
        while True:
            raw_len = fi.read(4)
            if len(raw_len) < 4:
                raise CorruptArchive("World file is truncated.")
            ct = fi.read(struct.unpack(">I", raw_len)[0])
            nonce = prefix + struct.pack(">I", counter)
            try:
                try:
                    fo.write(aes.decrypt(nonce, ct, b"\x00"))
                except InvalidTag:
                    fo.write(aes.decrypt(nonce, ct, b"\x01"))
                    if fi.read(1):
                        raise CorruptArchive("Extra data after the end of the world file.")
                    return
            except InvalidTag:
                raise CorruptArchive("World file is damaged or uses a different key.")
            counter += 1
