"""
dvr_config.py - Settings file for DVR Viewer.

Credentials are encrypted with Windows DPAPI, so the blob on disk is only
readable by this Windows user account on this machine. Camera URLs are
stored WITHOUT credentials and have them injected at connect time, so a
password never reaches the file in clear text. Everything else is plain
JSON you can hand-edit.
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
import re
import sys
import urllib.parse
from ctypes import wintypes
from dataclasses import asdict, dataclass, field
from typing import Any

# When frozen by PyInstaller, __file__ points inside the bundle, so it is no
# use for locating anything writable. Rebuilding the .exe also replaces its
# whole folder, so settings and captures go somewhere that survives that.
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
    DATA_DIR = os.path.join(
        os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
        "DVR Viewer")
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
    DATA_DIR = APP_DIR

try:
    os.makedirs(DATA_DIR, exist_ok=True)
except OSError:
    DATA_DIR = APP_DIR

CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
SNAPSHOT_DIR = os.path.join(DATA_DIR, "snapshots")
RECORDING_DIR = os.path.join(DATA_DIR, "recordings")


# --------------------------------------------------------------------------
# DPAPI protection
# --------------------------------------------------------------------------

class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


def _to_blob(data: bytes) -> tuple[_Blob, Any]:
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _dpapi(fn_name: str, data: bytes) -> bytes | None:
    if os.name != "nt":
        return None
    try:
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        fn = getattr(crypt32, fn_name)
        fn.restype = wintypes.BOOL
        blob_in, _keep = _to_blob(data)
        blob_out = _Blob()
        if not fn(ctypes.byref(blob_in), None, None, None, None, 0,
                  ctypes.byref(blob_out)):
            return None
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            kernel32.LocalFree(blob_out.pbData)
    except Exception:
        return None


def protect(secret: str) -> str:
    """Encrypt a secret for storage. Returns a tagged base64 string."""
    if not secret:
        return ""
    enc = _dpapi("CryptProtectData", secret.encode("utf-8"))
    if enc is not None:
        return "dpapi:" + base64.b64encode(enc).decode("ascii")
    # Fallback (non-Windows / DPAPI unavailable): obfuscation only.
    return "b64:" + base64.b64encode(secret.encode("utf-8")).decode("ascii")


def unprotect(stored: str) -> str:
    """Decrypt a stored secret back to plaintext."""
    if not stored:
        return ""
    if stored.startswith("dpapi:"):
        dec = _dpapi("CryptUnprotectData", base64.b64decode(stored[6:]))
        return dec.decode("utf-8", "replace") if dec is not None else ""
    if stored.startswith("b64:"):
        try:
            return base64.b64decode(stored[4:]).decode("utf-8", "replace")
        except Exception:
            return ""
    return stored  # legacy plaintext


# --------------------------------------------------------------------------
# URL credential handling
# --------------------------------------------------------------------------

_URL_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*)://(?:([^/@]*)@)?(.*)$", re.S)


def split_credentials(url: str) -> tuple[str, str, str]:
    """Pull inline credentials out of a URL -> (clean_url, user, password)."""
    m = _URL_RE.match(url or "")
    if not m:
        return url, "", ""
    scheme, cred, rest = m.groups()
    if not cred:
        return url, "", ""
    user, _, pw = cred.partition(":")
    return ("%s://%s" % (scheme, rest),
            urllib.parse.unquote(user), urllib.parse.unquote(pw))


def join_credentials(url: str, user: str, pw: str) -> str:
    """Put credentials back into a clean URL, percent-encoding them."""
    if not user:
        return url
    m = _URL_RE.match(url or "")
    if not m:
        return url
    scheme, _cred, rest = m.groups()
    return "%s://%s:%s@%s" % (scheme,
                              urllib.parse.quote(user, safe=""),
                              urllib.parse.quote(pw or "", safe=""),
                              rest)


# --------------------------------------------------------------------------
# Model
# --------------------------------------------------------------------------

@dataclass
class Camera:
    name: str = "Camera"
    url: str = ""            # stored WITHOUT credentials
    channel: int = 1
    transport: str = "tcp"
    enabled: bool = True
    cred_enc: str = ""       # DPAPI blob; empty = use the account below

    # -- credentials ------------------------------------------------------

    def set_credentials(self, user: str, pw: str) -> None:
        self.cred_enc = protect("%s\x00%s" % (user, pw)) if user else ""

    def credentials(self, cfg: "Config") -> tuple[str, str]:
        if self.cred_enc:
            raw = unprotect(self.cred_enc)
            user, _, pw = raw.partition("\x00")
            return user, pw
        return cfg.username, cfg.password

    def stream_url(self, cfg: "Config") -> str:
        """The real URL to hand to ffmpeg, credentials and all."""
        user, pw = self.credentials(cfg)
        return join_credentials(self.url, user, pw)

    # -- construction -----------------------------------------------------

    @staticmethod
    def from_url(name: str, url: str, channel: int = 1,
                 transport: str = "tcp",
                 cfg: "Config | None" = None) -> "Camera":
        """Build a camera from a URL that may carry inline credentials."""
        clean, user, pw = split_credentials(url)
        cam = Camera(name=name, url=clean, channel=channel,
                     transport=transport)
        # Only keep per-camera credentials if they differ from the account.
        if user and (cfg is None or (user, pw) != (cfg.username, cfg.password)):
            cam.set_credentials(user, pw)
        return cam

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Camera":
        cam = Camera(
            name=d.get("name", "Camera"),
            url=d.get("url", ""),
            channel=int(d.get("channel", 1)),
            transport=d.get("transport", "tcp"),
            enabled=bool(d.get("enabled", True)),
            cred_enc=d.get("cred_enc", ""),
        )
        # Migrate older files that stored the password inside the URL.
        clean, user, pw = split_credentials(cam.url)
        if user:
            cam.url = clean
            if not cam.cred_enc:
                cam.set_credentials(user, pw)
        return cam


@dataclass
class Config:
    ip: str = ""
    username: str = "admin"
    password_enc: str = ""
    channels: int = 8
    cameras: list[Camera] = field(default_factory=list)
    grid: str = "2x2"
    decode_height: int = 480      # per-tile decode height; lower = less CPU
    # Measured on this box: d3d11va decode of 1080p H.264 took 2.00s vs 1.79s
    # for pure software, because the frames have to be pulled back out of GPU
    # memory for the CPU scaler anyway. Off by default; flip it to true here
    # if a machine with a weaker CPU benefits.
    hw_accel: bool = False
    reconnect_secs: int = 5
    probe_timeout: float = 6.0
    snapshot_dir: str = SNAPSHOT_DIR
    recording_dir: str = RECORDING_DIR

    # -- password convenience ---------------------------------------------

    @property
    def password(self) -> str:
        return unprotect(self.password_enc)

    @password.setter
    def password(self, value: str) -> None:
        self.password_enc = protect(value)

    # -- persistence ------------------------------------------------------

    def to_dict(self) -> dict:
        d = asdict(self)
        d["cameras"] = [c.to_dict() for c in self.cameras]
        return d

    def save(self, path: str = CONFIG_PATH) -> None:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2)
        os.replace(tmp, path)

    @staticmethod
    def load(path: str = CONFIG_PATH) -> "Config":
        if not os.path.isfile(path):
            return Config()
        try:
            with open(path, "r", encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return Config()
        cfg = Config()
        for key, value in d.items():
            if key == "cameras":
                cfg.cameras = [Camera.from_dict(c) for c in value or []]
            elif hasattr(cfg, key):
                setattr(cfg, key, value)
        # A camera whose credentials match the account doesn't need its own
        # copy - drop it so changing the account password still propagates.
        for cam in cfg.cameras:
            if cam.cred_enc and cam.credentials(cfg) == (cfg.username,
                                                         cfg.password):
                cam.cred_enc = ""
        return cfg


def ensure_dirs(cfg: Config) -> None:
    for d in (cfg.snapshot_dir, cfg.recording_dir):
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass
