"""
dvr_core.py - Discovery, probing and stream engine for DVR Viewer.

Pure stdlib + ffmpeg. No GUI code lives here.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import socket
import subprocess
import threading
import time
import urllib.parse
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

# --------------------------------------------------------------------------
# ffmpeg location
# --------------------------------------------------------------------------

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# Where the app itself lives - next to the .exe once frozen, so someone can
# drop ffmpeg.exe beside it (or in an "ffmpeg" subfolder) to make the whole
# thing portable to a PC that has no ffmpeg installed.
if getattr(sys, "frozen", False):
    _SELF_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    _SELF_DIR = os.path.dirname(os.path.abspath(__file__))

_FFMPEG_HINTS = [
    _SELF_DIR,
    os.path.join(_SELF_DIR, "ffmpeg"),
    os.path.join(_SELF_DIR, "ffmpeg", "bin"),
    os.path.expandvars(
        r"%LOCALAPPDATA%\Microsoft\WinGet\Packages"
        r"\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
        r"\ffmpeg-8.0.1-full_build\bin"),
    r"C:\ffmpeg\bin",
    r"C:\Program Files\ffmpeg\bin",
]


def _find_tool(name: str) -> str:
    exe = name + (".exe" if os.name == "nt" else "")
    found = shutil.which(name)
    if found:
        return found
    for hint in _FFMPEG_HINTS:
        cand = os.path.join(hint, exe)
        if os.path.isfile(cand):
            return cand
    base = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages")
    if os.path.isdir(base):
        for root, _dirs, files in os.walk(base):
            if exe in files and os.path.basename(root).lower() == "bin":
                return os.path.join(root, exe)
    return name  # hope it is on PATH


FFMPEG = _find_tool("ffmpeg")
FFPROBE = _find_tool("ffprobe")


def ffmpeg_available() -> bool:
    try:
        subprocess.run([FFMPEG, "-version"], capture_output=True, timeout=10,
                       creationflags=CREATE_NO_WINDOW)
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------
# RTSP path library
# --------------------------------------------------------------------------
# Placeholders:
#   {ch}   1-based channel   (1, 2, 3 ...)
#   {ch0}  0-based channel   (0, 1, 2 ...)
#   {ch2}  1-based, 2 digits (01, 02 ...)
#   {user} {pw}  url-encoded credentials (vendors that put them in the path)

@dataclass(frozen=True)
class PathSpec:
    vendor: str
    main: str
    sub: str = ""
    port: int = 554


# Ordered roughly by how often they appear on consumer DVRs.
PATH_LIBRARY: list[PathSpec] = [
    PathSpec("Hikvision / Annke / Swann (ISAPI)",
             "/Streaming/Channels/{ch}01", "/Streaming/Channels/{ch}02"),
    PathSpec("Hikvision (legacy h264)",
             "/h264/ch{ch}/main/av_stream", "/h264/ch{ch}/sub/av_stream"),
    PathSpec("Hikvision (h265)",
             "/h265/ch{ch}/main/av_stream", "/h265/ch{ch}/sub/av_stream"),
    PathSpec("Dahua / Amcrest / Lorex",
             "/cam/realmonitor?channel={ch}&subtype=0",
             "/cam/realmonitor?channel={ch}&subtype=1"),
    PathSpec("XiongMai / XMeye / Sofia (common cheap DVR)",
             "/user={user}&password={pw}&channel={ch}&stream=0.sdp?",
             "/user={user}&password={pw}&channel={ch}&stream=1.sdp?"),
    PathSpec("XiongMai (real_stream variant)",
             "/user={user}&password={pw}&channel={ch}&stream=0.sdp?real_stream",
             "/user={user}&password={pw}&channel={ch}&stream=1.sdp?real_stream"),
    PathSpec("Generic NVR /chNN/N",
             "/ch{ch2}/0", "/ch{ch2}/1"),
    PathSpec("Generic NVR /live/chNN",
             "/live/ch{ch2}_0", "/live/ch{ch2}_1"),
    PathSpec("Generic /live/N/main",
             "/live/{ch}/main", "/live/{ch}/sub"),
    PathSpec("Reolink",
             "/h264Preview_{ch2}_main", "/h264Preview_{ch2}_sub"),
    PathSpec("Reolink (h265)",
             "/Preview_{ch2}_main", "/Preview_{ch2}_sub"),
    PathSpec("Uniview",
             "/media/video{ch}", "/media/video{ch}"),
    PathSpec("Uniview (unicast)",
             "/unicast/c{ch}/s0/live", "/unicast/c{ch}/s1/live"),
    PathSpec("TP-Link / Tapo / VIGI",
             "/stream{ch}", "/stream{ch}"),
    PathSpec("ONVIF-style profileN",
             "/profile{ch}", "/profile{ch}"),
    PathSpec("ONVIF-style onvifN",
             "/onvif{ch}", "/onvif{ch}"),
    PathSpec("Hanwha / Samsung Wisenet",
             "/profile{ch}/media.smp", "/profile{ch}/media.smp"),
    PathSpec("Axis",
             "/axis-media/media.amp?camera={ch}",
             "/axis-media/media.amp?camera={ch}&resolution=640x480"),
    PathSpec("Vivotek",
             "/live{ch}.sdp", "/live{ch}.sdp"),
    PathSpec("Foscam",
             "/videoMain", "/videoSub"),
    PathSpec("Generic /avN_N",
             "/av{ch0}_0", "/av{ch0}_1"),
    PathSpec("Generic /camN_N",
             "/cam{ch0}_0", "/cam{ch0}_1"),
    PathSpec("Numeric short (Chinese NVR)",
             "/{ch}1", "/{ch}2"),
    PathSpec("Bare channel number",
             "/{ch}", "/{ch}"),
    PathSpec("Bare zero-based",
             "/{ch0}", "/{ch0}"),
    PathSpec("Streaming/Unicast",
             "/Streaming/Unicast/channels/{ch}01",
             "/Streaming/Unicast/channels/{ch}02"),
    PathSpec("Provision-ISR / generic chNN",
             "/ch{ch2}/main/av_stream", "/ch{ch2}/sub/av_stream"),
    PathSpec("Instar / generic 11-12",
             "/11", "/12"),
    PathSpec("Generic /videoN",
             "/video{ch}", "/video{ch}"),
    PathSpec("Generic /videostream.asf",
             "/videostream.asf", "/videostream.asf"),
    PathSpec("Root (bare, some encoders)",
             "", ""),
    PathSpec("Sricam / onvif media",
             "/media/video{ch0}", "/media/video{ch0}"),
    PathSpec("Longse / generic mainN",
             "/live/main{ch}", "/live/sub{ch}"),
    PathSpec("Hikvision on port 8554",
             "/Streaming/Channels/{ch}01",
             "/Streaming/Channels/{ch}02", 8554),
    PathSpec("Dahua on port 8554",
             "/cam/realmonitor?channel={ch}&subtype=0",
             "/cam/realmonitor?channel={ch}&subtype=1", 8554),
]

# Some budget recorders never expose RTSP at all but do serve MJPEG over
# HTTP. Tried only as a fallback, once every RTSP path has failed.
HTTP_LIBRARY: list[PathSpec] = [
    PathSpec("Hikvision httpPreview",
             "/ISAPI/Streaming/channels/{ch}01/httpPreview", "", 80),
    PathSpec("Dahua MJPEG CGI",
             "/cgi-bin/mjpg/video.cgi?channel={ch}&subtype=1", "", 80),
    PathSpec("Foscam / generic videostream.cgi",
             "/videostream.cgi?user={user}&pwd={pw}", "", 80),
    PathSpec("Axis MJPEG",
             "/mjpg/video.mjpg", "", 80),
    PathSpec("Generic /video.cgi",
             "/video.cgi", "", 80),
    PathSpec("Generic /mjpeg",
             "/mjpeg", "", 80),
    PathSpec("XiongMai webcapture",
             "/webcapture.jpg?command=snap&channel={ch}", "", 80),
    PathSpec("Generic snapshot CGI",
             "/cgi-bin/snapshot.cgi?channel={ch}", "", 80),
]

# Ports worth knocking on when fingerprinting a recorder.
COMMON_PORTS: dict[int, str] = {
    80: "HTTP web UI",
    443: "HTTPS web UI",
    554: "RTSP (standard)",
    555: "RTSP (alt)",
    8000: "Hikvision SDK / HTTP alt",
    8080: "HTTP alt",
    8554: "RTSP (alt)",
    8899: "ONVIF (XiongMai)",
    9000: "HTTP alt",
    10554: "RTSP (alt)",
    34567: "XiongMai / XMeye media port",
    37777: "Dahua SDK port",
    5000: "HTTP alt / ONVIF",
    2000: "ONVIF alt",
    88: "Foscam HTTP",
}


def enc(s: str) -> str:
    """URL-encode a credential so @ : / do not break the URL."""
    return urllib.parse.quote(s or "", safe="")


def build_url(ip: str, port: int, path: str, user: str, pw: str,
              channel: int, scheme: str = "rtsp") -> str:
    """Render a PathSpec template into a full stream URL."""
    path = (path
            .replace("{ch2}", "%02d" % channel)
            .replace("{ch0}", str(channel - 1))
            .replace("{ch}", str(channel))
            .replace("{user}", enc(user))
            .replace("{pw}", enc(pw)))
    cred = "%s:%s@" % (enc(user), enc(pw)) if user else ""
    if path and not path.startswith("/"):
        path = "/" + path
    return "%s://%s%s:%d%s" % (scheme, cred, ip, port, path)


def redact(url: str) -> str:
    """Hide the password when a URL is shown in the UI or written to a log."""
    return re.sub(r"((?:rtsp|http|https)://[^:/@]+:)[^@]*(@)",
                  r"\1*****\2", url)


# --------------------------------------------------------------------------
# Probe results
# --------------------------------------------------------------------------

OK = "ok"
AUTH_FAIL = "auth"
NOT_FOUND = "not_found"
NO_ROUTE = "no_route"
TIMEOUT = "timeout"
ERROR = "error"

_STATUS_LABEL = {
    OK: "WORKS",
    AUTH_FAIL: "wrong username/password",
    NOT_FOUND: "path not on this device",
    NO_ROUTE: "port closed / refused",
    TIMEOUT: "timed out",
    ERROR: "error",
}


@dataclass
class ProbeResult:
    url: str
    status: str
    vendor: str = ""
    channel: int = 1
    transport: str = "tcp"
    codec: str = ""
    width: int = 0
    height: int = 0
    fps: float = 0.0
    audio: str = ""
    detail: str = ""
    elapsed: float = 0.0

    @property
    def works(self) -> bool:
        return self.status == OK

    @property
    def label(self) -> str:
        return _STATUS_LABEL.get(self.status, self.status)

    def summary(self) -> str:
        if self.works:
            res = "%dx%d" % (self.width, self.height) if self.width else "?"
            fps = " @%.0ffps" % self.fps if self.fps else ""
            aud = " +%s" % self.audio if self.audio else ""
            return "%s %s%s%s (%s)" % (self.codec, res, fps, aud,
                                       self.transport.upper())
        return self.detail or self.label


def _classify(stderr: str) -> tuple[str, str]:
    # ffmpeg echoes the URL back, and our own "?timeout=6000000" inside it
    # would otherwise be read as evidence of a timeout. Strip query strings
    # and the option text before looking for keywords.
    s = re.sub(r"timeout=\d+", " ", stderr.lower())
    s = re.sub(r"\?\S*", " ", s)
    first_err = ""
    for line in stderr.splitlines():
        line = line.strip()
        if line and not line.startswith("["):
            first_err = line
            break
    if not first_err and stderr.strip():
        first_err = stderr.strip().splitlines()[-1]

    if "401" in s or "unauthorized" in s:
        return AUTH_FAIL, "401 Unauthorized - credentials rejected"
    if "403" in s or "forbidden" in s:
        return AUTH_FAIL, "403 Forbidden - user lacks live-view rights"
    if "404" in s or "not found" in s:
        return NOT_FOUND, "404 Not Found - no stream at this path"
    if "455" in s or "method not valid" in s:
        return NOT_FOUND, "455 - wrong path/state"
    if "503" in s or "service unavailable" in s:
        return ERROR, "503 - device busy / max streams reached"
    if "connection refused" in s or "10061" in s:
        return NO_ROUTE, "connection refused - no RTSP on this port"
    if "no route to host" in s or "unreachable" in s:
        return NO_ROUTE, "host unreachable"
    if "timed out" in s or "timeout" in s or "10060" in s:
        return TIMEOUT, "timed out - no RTSP reply"
    if "immediate exit" in s:
        return TIMEOUT, "timed out"
    # Windows ffmpeg collapses "refused" and "timed out" into one opaque code.
    if "error number -138" in s:
        return TIMEOUT, "no reply on this port (refused or timed out)"
    if "invalid data" in s or "could not find codec" in s:
        return ERROR, "connected but stream unreadable"
    return ERROR, (first_err[:160] or "unknown error")


def probe_url(url: str, timeout: float = 6.0, transport: str = "tcp",
              vendor: str = "", channel: int = 1) -> ProbeResult:
    """Ask ffprobe whether this RTSP URL actually yields video."""
    t0 = time.time()
    cmd = [FFPROBE, "-hide_banner", "-v", "error"]
    # These are RTSP demuxer options; an HTTP source rejects them.
    if url.lower().startswith("rtsp"):
        cmd += ["-rtsp_transport", transport,
                "-timeout", str(int(timeout * 1_000_000))]
    else:
        cmd += ["-rw_timeout", str(int(timeout * 1_000_000))]
    cmd += ["-analyzeduration", "3000000",
            "-probesize", "2000000",
            "-print_format", "json",
            "-show_streams", "-show_format",
            url]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout + 6,
                           creationflags=CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        return ProbeResult(url, TIMEOUT, vendor, channel, transport,
                           detail="ffprobe hard timeout",
                           elapsed=time.time() - t0)
    except FileNotFoundError:
        return ProbeResult(url, ERROR, vendor, channel, transport,
                           detail="ffprobe not found on this PC",
                           elapsed=time.time() - t0)

    elapsed = time.time() - t0
    out = p.stdout.decode("utf-8", "replace")
    err = p.stderr.decode("utf-8", "replace")

    data = {}
    if out.strip():
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            data = {}

    vstream = next((s for s in data.get("streams", [])
                    if s.get("codec_type") == "video"), None)
    if vstream is None:
        status, detail = _classify(err)
        if status == OK:
            status, detail = ERROR, "no video stream returned"
        return ProbeResult(url, status, vendor, channel, transport,
                           detail=detail, elapsed=elapsed)

    astream = next((s for s in data.get("streams", [])
                    if s.get("codec_type") == "audio"), None)

    fps = 0.0
    for key in ("avg_frame_rate", "r_frame_rate"):
        raw = vstream.get(key) or ""
        if "/" in raw:
            try:
                num, den = raw.split("/")
                if float(den):
                    fps = float(num) / float(den)
                    if fps:
                        break
            except (ValueError, ZeroDivisionError):
                pass

    return ProbeResult(
        url=url, status=OK, vendor=vendor, channel=channel,
        transport=transport,
        codec=(vstream.get("codec_name") or "?").upper(),
        width=int(vstream.get("width") or 0),
        height=int(vstream.get("height") or 0),
        fps=fps,
        audio=(astream.get("codec_name", "").upper() if astream else ""),
        elapsed=elapsed,
    )


# --------------------------------------------------------------------------
# Port scan / fingerprint
# --------------------------------------------------------------------------

def check_port(ip: str, port: int, timeout: float = 1.2) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def scan_ports(ip: str, ports: Optional[Iterable[int]] = None,
               timeout: float = 1.2) -> dict[int, bool]:
    ports = list(ports or COMMON_PORTS.keys())
    results: dict[int, bool] = {}
    lock = threading.Lock()

    def worker(p: int) -> None:
        alive = check_port(ip, p, timeout)
        with lock:
            results[p] = alive

    threads = [threading.Thread(target=worker, args=(p,), daemon=True)
               for p in ports]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout + 3)
    return results


def rtsp_options(ip: str, port: int = 554, timeout: float = 4.0) -> str:
    """Raw RTSP OPTIONS request. The Server: header usually names the vendor."""
    req = ("OPTIONS rtsp://%s:%d/ RTSP/1.0\r\n"
           "CSeq: 1\r\n"
           "User-Agent: DVRViewer\r\n\r\n" % (ip, port))
    try:
        with socket.create_connection((ip, port), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(req.encode())
            return s.recv(4096).decode("utf-8", "replace")
    except OSError as e:
        return "<no reply: %s>" % e


def http_banner(ip: str, port: int = 80, timeout: float = 4.0) -> str:
    req = ("GET / HTTP/1.1\r\nHost: %s\r\n"
           "User-Agent: DVRViewer\r\nConnection: close\r\n\r\n" % ip)
    try:
        with socket.create_connection((ip, port), timeout=timeout) as s:
            s.settimeout(timeout)
            chunks: list[bytes] = []
            s.sendall(req.encode())
            while sum(len(c) for c in chunks) < 6000:
                b = s.recv(4096)
                if not b:
                    break
                chunks.append(b)
            return b"".join(chunks).decode("utf-8", "replace")
    except OSError as e:
        return "<no reply: %s>" % e


_VENDOR_SIGNS = [
    (r"hikvision|dvrdvs|isapi|hiwatch",
     "Hikvision (or OEM: Annke, Swann, LaView, HiWatch)"),
    (r"dahua|lechange|amcrest|imou",
     "Dahua (or OEM: Amcrest, Lorex, Imou)"),
    (r"xiongmai|netsurveillance|sofia|hi3520|xmeye",
     "XiongMai / XMeye (generic Chinese DVR)"),
    (r"uniview|\bunv\b", "Uniview"),
    (r"reolink", "Reolink"),
    (r"\baxis\b", "Axis"),
    (r"hanwha|samsung techwin|wisenet", "Hanwha / Wisenet"),
    (r"vivotek", "Vivotek"),
    (r"tp-link|vigi|tapo", "TP-Link"),
    (r"foscam", "Foscam"),
    (r"provision", "Provision-ISR"),
    (r"longse", "Longse"),
    (r"boa/|thttpd|lighttpd", "embedded Linux DVR (generic)"),
]


def guess_vendor(*texts: str) -> str:
    blob = "\n".join(texts).lower()
    hits = [name for pat, name in _VENDOR_SIGNS if re.search(pat, blob, re.I)]
    return " / ".join(dict.fromkeys(hits)) if hits else ""


def fingerprint(ip: str, log: Callable[[str], None] = print) -> dict:
    """Knock on every likely port and try to name the recorder."""
    log("Scanning ports on %s ..." % ip)
    ports = scan_ports(ip)
    open_ports = sorted(p for p, alive in ports.items() if alive)
    if open_ports:
        for p in open_ports:
            log("  OPEN  %-6d %s" % (p, COMMON_PORTS.get(p, "")))
    else:
        log("  No common ports open. Wrong IP, device off, "
            "or a firewall is in the way.")

    banners: list[str] = []
    for p in [x for x in open_ports if x in (554, 555, 8554, 10554)]:
        log("Asking the RTSP server on port %d who it is ..." % p)
        r = rtsp_options(ip, p)
        banners.append(r)
        for line in r.splitlines():
            if line.lower().startswith(("server:", "public:", "rtsp/1.0")):
                log("  " + line.strip())

    for p in [x for x in open_ports if x in (80, 8080, 8000, 88, 9000, 5000)]:
        log("Reading the HTTP banner on port %d ..." % p)
        r = http_banner(ip, p)
        banners.append(r)
        for line in r.splitlines()[:14]:
            ls = line.lower()
            if ls.startswith(("server:", "www-authenticate:", "http/")) \
                    or "<title" in ls:
                log("  " + line.strip()[:110])

    vendor = guess_vendor(*banners)
    if vendor:
        log("\nLooks like: %s" % vendor)
    else:
        log("\nCouldn't name the vendor from the banners - "
            "the path scan will find it anyway.")

    return {"ip": ip, "ports": ports, "open": open_ports,
            "vendor": vendor, "banners": banners}


# --------------------------------------------------------------------------
# Path discovery
# --------------------------------------------------------------------------

def discover_streams(ip: str, user: str, pw: str,
                     channels: Iterable[int] = (1,),
                     ports: Optional[Iterable[int]] = None,
                     timeout: float = 6.0,
                     workers: int = 6,
                     stop_flag: Optional[threading.Event] = None,
                     log: Callable[[str], None] = print,
                     try_udp: bool = True,
                     try_http: bool = True) -> list[ProbeResult]:
    """
    Try every path in PATH_LIBRARY against the given channels until video
    comes back. Returns every candidate that produced real video.
    """
    channels = list(channels)
    open_rtsp = list(ports) if ports else []
    if not open_rtsp:
        open_rtsp = [p for p in (554, 8554, 555, 10554, 8000)
                     if check_port(ip, p, 1.5)]
    if not open_rtsp:
        # Sweeping a port with nothing behind it just burns one full timeout
        # per candidate path, so don't.
        log("Nothing is listening on any RTSP port (554, 555, 8554, 10554).")
        log("Skipping the RTSP sweep - there is no server there to answer it.")
        if try_http and (stop_flag is None or not stop_flag.is_set()):
            return discover_http(ip, user, pw, channels, timeout=timeout,
                                 stop_flag=stop_flag, log=log)
        return []
    log("RTSP ports to try: %s" % ", ".join(str(p) for p in open_rtsp))

    jobs: list[tuple[int, PathSpec, int]] = []
    for port in open_rtsp:
        for spec in PATH_LIBRARY:
            if spec.port != 554 and spec.port != port:
                continue
            for ch in channels:
                jobs.append((port, spec, ch))

    total = len(jobs)
    log("Testing %d candidate URLs (%d known layouts x %d channel(s)) ..."
        % (total, len(PATH_LIBRARY), len(channels)))

    found: list[ProbeResult] = []
    lock = threading.Lock()
    counter = {"n": 0, "auth": 0}
    good_specs: set[str] = set()

    def worker(job: tuple[int, PathSpec, int]) -> None:
        port, spec, ch = job
        if stop_flag is not None and stop_flag.is_set():
            return
        with lock:
            if spec.vendor in good_specs:
                return
        url = build_url(ip, port, spec.main, user, pw, ch)
        res = probe_url(url, timeout, "tcp", spec.vendor, ch)
        if res.status in (TIMEOUT, ERROR) and try_udp:
            if stop_flag is None or not stop_flag.is_set():
                alt = probe_url(url, timeout, "udp", spec.vendor, ch)
                if alt.works:
                    res = alt
        with lock:
            counter["n"] += 1
            n = counter["n"]
            if res.status == AUTH_FAIL:
                counter["auth"] += 1
            if res.works:
                good_specs.add(spec.vendor)
                found.append(res)
                log("[%d/%d]  FOUND  ch%d  %s" % (n, total, ch, spec.vendor))
                log("          %s" % redact(url))
                log("          %s" % res.summary())
            elif n % 12 == 0:
                log("[%d/%d] ... %s: %s" % (n, total, res.label, spec.vendor))

    idx = 0
    while idx < len(jobs):
        if stop_flag is not None and stop_flag.is_set():
            log("Scan cancelled.")
            break
        batch = jobs[idx:idx + workers]
        idx += workers
        threads = [threading.Thread(target=worker, args=(j,), daemon=True)
                   for j in batch]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout * 2 + 12)

    if found:
        log("\nDone. %d working stream layout(s) found." % len(found))
        return found

    log("\nNo working RTSP path found.")
    if counter["auth"]:
        log("NOTE: %d paths answered '401 Unauthorized'. That means the RTSP "
            "path is probably RIGHT but the username/password is wrong, or "
            "that user has no live-view rights. Check the DVR's user list."
            % counter["auth"])

    if try_http and (stop_flag is None or not stop_flag.is_set()):
        found = discover_http(ip, user, pw, channels, timeout=timeout,
                              stop_flag=stop_flag, log=log)
    if not found and not counter["auth"]:
        log("\nNothing answered 401 either, so the recorder may have RTSP "
            "switched off. Look in its menu under "
            "Network > Advanced > RTSP / ONVIF and enable it.")
    return found


def discover_http(ip: str, user: str, pw: str,
                  channels: Iterable[int] = (1,),
                  ports: Optional[Iterable[int]] = None,
                  timeout: float = 6.0,
                  stop_flag: Optional[threading.Event] = None,
                  log: Callable[[str], None] = print) -> list[ProbeResult]:
    """Last resort: look for an MJPEG-over-HTTP stream instead of RTSP."""
    channels = list(channels)
    http_ports = list(ports) if ports else [
        p for p in (80, 8080, 8000, 88) if check_port(ip, p, 1.5)]
    if not http_ports:
        return []

    log("\nTrying HTTP/MJPEG instead, on port(s) %s ..."
        % ", ".join(str(p) for p in http_ports))
    found: list[ProbeResult] = []
    for port in http_ports:
        for spec in HTTP_LIBRARY:
            if stop_flag is not None and stop_flag.is_set():
                return found
            for ch in channels:
                url = build_url(ip, port, spec.main, user, pw, ch,
                                scheme="http")
                res = probe_url(url, timeout, "tcp", spec.vendor, ch)
                if res.works:
                    found.append(res)
                    log("  FOUND  %s" % spec.vendor)
                    log("         %s" % redact(url))
                    log("         %s" % res.summary())
                    break
            if found:
                break
        if found:
            break
    if not found:
        log("  No HTTP/MJPEG stream either.")
    return found


def expand_channels(found: list[ProbeResult], ip: str, user: str, pw: str,
                    max_channels: int = 16, timeout: float = 6.0,
                    log: Callable[[str], None] = print,
                    stop_flag: Optional[threading.Event] = None
                    ) -> list[ProbeResult]:
    """Once one channel works, walk the same layout across all channels."""
    if not found:
        return []
    template = found[0]
    spec = next((s for s in PATH_LIBRARY + HTTP_LIBRARY
                 if s.vendor == template.vendor), None)
    if spec is None:
        return found
    parsed = urllib.parse.urlparse(template.url)
    scheme = parsed.scheme or "rtsp"
    port = parsed.port or (554 if scheme == "rtsp" else 80)

    log("\nWalking '%s' across channels 1-%d ..." % (spec.vendor, max_channels))
    results: list[ProbeResult] = []
    lock = threading.Lock()

    def worker(ch: int) -> None:
        if stop_flag is not None and stop_flag.is_set():
            return
        url = build_url(ip, port, spec.main, user, pw, ch, scheme=scheme)
        res = probe_url(url, timeout, template.transport, spec.vendor, ch)
        with lock:
            if res.works:
                results.append(res)
                log("  ch%-3d OK   %s" % (ch, res.summary()))
            else:
                log("  ch%-3d --   %s" % (ch, res.label))

    chans = list(range(1, max_channels + 1))
    for i in range(0, len(chans), 4):
        threads = [threading.Thread(target=worker, args=(c,), daemon=True)
                   for c in chans[i:i + 4]]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout * 2 + 12)
        if stop_flag is not None and stop_flag.is_set():
            break

    results.sort(key=lambda r: r.channel)
    log("%d channel(s) live." % len(results))
    return results
