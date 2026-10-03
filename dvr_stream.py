"""
dvr_stream.py - Live video decoding for DVR Viewer.

One ffmpeg process per camera. ffmpeg does the RTSP work, the decoding and
the scaling; we just read fixed-size raw RGB frames off its stdout and turn
them into QImages. That keeps Python out of the hot path and makes a dozen
cameras viable.
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from collections import deque
from datetime import datetime

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

from dvr_core import FFMPEG, CREATE_NO_WINDOW, redact

# Connection states
CONNECTING = "connecting"
LIVE = "live"
RETRYING = "retrying"
STOPPED = "stopped"
FAILED = "failed"


def _even(n: int) -> int:
    n = max(16, int(n))
    return n - (n % 2)


class StreamWorker(QThread):
    """Runs ffmpeg for one camera and emits decoded frames."""

    frame_ready = Signal(QImage)
    state_changed = Signal(str, str)      # state, human-readable message
    stats_ready = Signal(float, float)    # fps, megabits/sec

    def __init__(self, url: str, width: int = 854, height: int = 480,
                 transport: str = "tcp", hw_accel: bool = True,
                 reconnect_secs: int = 5, parent=None) -> None:
        super().__init__(parent)
        self.url = url
        self.width = _even(width)
        self.height = _even(height)
        self.transport = transport
        self.hw_accel = hw_accel
        self.reconnect_secs = max(1, int(reconnect_secs))

        self._stop = threading.Event()
        self._restart = threading.Event()
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._last_error = ""
        self._latest: QImage | None = None
        self._latest_lock = threading.Lock()

    # -- public API -------------------------------------------------------

    def stop(self) -> None:
        self._stop.set()
        self._kill_proc()

    def set_size(self, width: int, height: int) -> None:
        """Change the decode resolution; restarts ffmpeg."""
        w, h = _even(width), _even(height)
        if (w, h) == (self.width, self.height):
            return
        self.width, self.height = w, h
        self._restart.set()
        self._kill_proc()

    def snapshot(self) -> QImage | None:
        with self._latest_lock:
            return self._latest.copy() if self._latest is not None else None

    # -- internals --------------------------------------------------------

    def _kill_proc(self) -> None:
        with self._lock:
            proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass

    def _build_cmd(self, use_hw: bool) -> list[str]:
        pad = ("scale=%d:%d:force_original_aspect_ratio=decrease,"
               "pad=%d:%d:-1:-1:color=#0e0e12"
               % (self.width, self.height, self.width, self.height))
        low = self.url.lower()
        is_rtsp = low.startswith("rtsp")
        is_live = is_rtsp or low.startswith(("http://", "https://"))

        cmd = [FFMPEG, "-hide_banner", "-loglevel", "warning"]
        # RTSP-only options; an HTTP/MJPEG or file source rejects them.
        if is_rtsp:
            cmd += ["-rtsp_transport", self.transport, "-timeout", "10000000"]
        # The low-latency flags belong to live sources only. On a seekable
        # file "nobuffer" makes ffmpeg emit no frames at all.
        if is_live:
            cmd += ["-fflags", "nobuffer", "-flags", "low_delay"]
        cmd += ["-analyzeduration", "1500000", "-probesize", "1500000"]
        if use_hw:
            cmd += ["-hwaccel", "d3d11va"]
        cmd += ["-i", self.url,
                "-an", "-sn", "-dn",
                "-vf", pad,
                "-pix_fmt", "rgb24",
                "-f", "rawvideo",
                "-fps_mode", "passthrough",
                "-"]
        return cmd

    def _drain_stderr(self, proc: subprocess.Popen) -> None:
        try:
            for raw in iter(proc.stderr.readline, b""):
                line = raw.decode("utf-8", "replace").strip()
                if line:
                    self._last_error = line
        except (OSError, ValueError):
            pass

    def run(self) -> None:  # noqa: C901 - a connect/read/retry loop
        use_hw = self.hw_accel
        attempt = 0

        while not self._stop.is_set():
            self._restart.clear()
            self.state_changed.emit(CONNECTING, "connecting...")
            frame_bytes = self.width * self.height * 3
            cmd = self._build_cmd(use_hw)

            try:
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    bufsize=frame_bytes * 2,
                    creationflags=CREATE_NO_WINDOW)
            except (OSError, FileNotFoundError) as exc:
                self.state_changed.emit(
                    FAILED, "cannot start ffmpeg: %s" % exc)
                return

            with self._lock:
                self._proc = proc
            threading.Thread(target=self._drain_stderr, args=(proc,),
                             daemon=True).start()

            got_frame = False
            started = time.time()
            times: deque[float] = deque(maxlen=60)
            bytes_seen = 0
            last_stat = time.time()

            while not self._stop.is_set() and not self._restart.is_set():
                try:
                    buf = proc.stdout.read(frame_bytes)
                except (OSError, ValueError):
                    break
                if not buf or len(buf) < frame_bytes:
                    break

                img = QImage(buf, self.width, self.height,
                             self.width * 3, QImage.Format_RGB888).copy()
                with self._latest_lock:
                    self._latest = img

                if not got_frame:
                    got_frame = True
                    attempt = 0
                    hw_note = " (GPU decode)" if use_hw else ""
                    self.state_changed.emit(LIVE, "live" + hw_note)
                self.frame_ready.emit(img)

                now = time.time()
                times.append(now)
                bytes_seen += frame_bytes
                if now - last_stat >= 1.0:
                    fps = 0.0
                    if len(times) > 1:
                        span = times[-1] - times[0]
                        if span > 0:
                            fps = (len(times) - 1) / span
                    mbps = (bytes_seen * 8) / (now - last_stat) / 1_000_000
                    self.stats_ready.emit(fps, mbps)
                    bytes_seen = 0
                    last_stat = now

            self._kill_proc()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass

            if self._stop.is_set():
                break
            if self._restart.is_set():
                continue  # size change: reconnect immediately

            # Hardware decode failed before a single frame -> drop to software.
            if not got_frame and use_hw and time.time() - started < 25:
                use_hw = False
                self.state_changed.emit(
                    RETRYING, "GPU decode failed, using CPU...")
                continue

            # Never got a frame over TCP -> try UDP once.
            if (not got_frame and self.transport == "tcp" and attempt == 1
                    and self.url.lower().startswith("rtsp")):
                self.transport = "udp"
                self.state_changed.emit(RETRYING, "TCP failed, trying UDP...")
                continue

            attempt += 1
            wait = min(self.reconnect_secs * min(attempt, 4), 30)
            msg = self._last_error[:90] or "stream dropped"
            self.state_changed.emit(
                RETRYING, "%s - retry in %ds" % (msg, wait))
            for _ in range(wait * 10):
                if self._stop.is_set() or self._restart.is_set():
                    break
                time.sleep(0.1)

        self.state_changed.emit(STOPPED, "stopped")


class Recorder:
    """Copies a live stream straight to an .mp4 - no re-encoding, no CPU."""

    def __init__(self, url: str, out_dir: str, name: str,
                 transport: str = "tcp") -> None:
        self.url = url
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        safe = "".join(c if c.isalnum() or c in "-_ " else "_"
                       for c in name).strip() or "camera"
        os.makedirs(out_dir, exist_ok=True)
        self.path = os.path.join(out_dir, "%s_%s.mp4" % (safe, stamp))
        self.transport = transport
        self.proc: subprocess.Popen | None = None

    def start(self) -> bool:
        cmd = [FFMPEG, "-hide_banner", "-loglevel", "error"]
        if self.url.lower().startswith("rtsp"):
            cmd += ["-rtsp_transport", self.transport, "-timeout", "10000000"]
        cmd += ["-i", self.url,
                "-c", "copy",
                "-movflags", "+faststart",
                "-y", self.path]
        try:
            self.proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
            return True
        except OSError:
            self.proc = None
            return False

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self) -> str:
        """Ask ffmpeg to finalise the file, then return its path."""
        if self.proc is None:
            return self.path
        try:
            if self.proc.poll() is None:
                self.proc.stdin.write(b"q")
                self.proc.stdin.flush()
                self.proc.wait(timeout=6)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            try:
                self.proc.kill()
            except OSError:
                pass
        return self.path


class AudioPlayer:
    """Plays just the audio of one camera through ffplay."""

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.url = ""

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def play(self, url: str, transport: str = "tcp") -> bool:
        self.stop()
        ffplay = FFMPEG.replace("ffmpeg.exe", "ffplay.exe") \
            .replace("ffmpeg", "ffplay") if FFMPEG else "ffplay"
        cmd = [ffplay, "-hide_banner", "-loglevel", "error",
               "-nodisp", "-autoexit", "-vn"]
        if url.lower().startswith("rtsp"):
            cmd += ["-rtsp_transport", transport]
        cmd += ["-fflags", "nobuffer", url]
        try:
            self.proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW)
            self.url = url
            return True
        except OSError:
            self.proc = None
            return False

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.kill()
            except OSError:
                pass
        self.proc = None
        self.url = ""


def describe(url: str) -> str:
    return redact(url)
