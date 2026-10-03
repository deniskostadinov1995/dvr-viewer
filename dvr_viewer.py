"""
DVR Viewer - a Windows viewer for DVR/NVR cameras over RTSP.

Run:  py -3.13 dvr_viewer.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import (QAction, QColor, QGuiApplication, QImage,
                           QKeySequence, QPainter, QPen)
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFormLayout, QFrame, QGridLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QSizePolicy,
    QSpinBox, QSplitter, QStackedWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget)

import dvr_core as core
from dvr_config import APP_DIR, Camera, Config, ensure_dirs
from dvr_stream import (AudioPlayer, CONNECTING, LIVE, RETRYING, Recorder,
                        STOPPED, StreamWorker)

APP_NAME = "DVR Viewer"

GRID_COLS = {"1x1": 1, "2x2": 2, "3x3": 3, "4x4": 4}

QUALITY = {
    "Low (360p) - lightest": 360,
    "Medium (480p)": 480,
    "High (720p)": 720,
    "Full (1080p) - heaviest": 1080,
}

DARK_QSS = """
QWidget { background: #0e0e12; color: #d7d9e0;
          font-family: 'Segoe UI'; font-size: 13px; }
QMainWindow, QDialog { background: #0e0e12; }

QPushButton {
    background: #1b1c24; border: 1px solid #2a2c37; border-radius: 6px;
    padding: 7px 14px; color: #d7d9e0;
}
QPushButton:hover   { background: #24252f; border-color: #3a3d4d; }
QPushButton:pressed { background: #14151b; }
QPushButton:disabled{ color: #55586a; border-color: #1f2029; }
QPushButton#accent {
    background: #1f6feb; border: 1px solid #1f6feb; color: #ffffff;
    font-weight: 600;
}
QPushButton#accent:hover    { background: #2f7ff5; }
QPushButton#accent:disabled { background: #1d2b45; border-color: #1d2b45;
                              color: #6b7385; }
QPushButton#danger { background: #2a1418; border: 1px solid #5a2630;
                     color: #ef8090; }
QPushButton#danger:hover { background: #3a1a20; }
QPushButton#nav { background: transparent; border: 1px solid transparent;
                  color: #8f94a6; padding: 6px 14px; }
QPushButton#nav:hover   { color: #e8eaf0; background: #1b1c24; }
QPushButton#nav:checked { color: #ffffff; background: #1b1c24;
                          border-color: #2a2c37; font-weight: 600; }
QPushButton#tile {
    background: #16171e; border: 1px solid #262832;
    border-radius: 4px; padding: 3px 9px; font-size: 11px; color: #aeb2c0;
}
QPushButton#tile:hover   { background: #24252f; color: #e8eaf0; }
QPushButton#tile:checked { background: #7a1f2b; border-color: #b03040;
                           color: #ffd8dd; }

QLineEdit, QSpinBox, QComboBox, QPlainTextEdit {
    background: #14151b; border: 1px solid #2a2c37; border-radius: 6px;
    padding: 6px 8px; selection-background-color: #1f6feb;
}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border-color: #1f6feb; }
QComboBox::drop-down { border: none; width: 20px; }
QComboBox QAbstractItemView {
    background: #14151b; border: 1px solid #2a2c37;
    selection-background-color: #1f6feb;
}
QPlainTextEdit {
    font-family: 'Cascadia Mono','Consolas',monospace; font-size: 12px;
    color: #b8c0d0;
}

QGroupBox {
    border: 1px solid #23242e; border-radius: 8px;
    margin-top: 16px; padding-top: 10px; font-weight: 600; color: #8f94a6;
}
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; }

QTableWidget {
    background: #14151b; border: 1px solid #23242e; border-radius: 8px;
    gridline-color: #23242e; selection-background-color: #1c3355;
}
QHeaderView::section {
    background: #1b1c24; color: #8f94a6; border: none;
    border-bottom: 1px solid #2a2c37; padding: 7px; font-weight: 600;
}

QLabel#h1 { font-size: 19px; font-weight: 600; color: #eef0f5; }
QLabel#hint { color: #7d8296; font-size: 12px; }
QLabel#status { color: #8f94a6; font-size: 12px; }
QLabel#pill { background: #14151b; border: 1px solid #23242e;
              border-radius: 10px; padding: 3px 10px; color: #8f94a6;
              font-size: 11px; }

QFrame#toolbar { background: #12131a; border: none;
                 border-bottom: 1px solid #23242e; }
QScrollBar:vertical { background: #0e0e12; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #2a2c37; border-radius: 5px;
                              min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #3a3d4d; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
QSplitter::handle { background: #1a1b22; width: 4px; }
"""


# ==========================================================================
# Video surface
# ==========================================================================

class VideoSurface(QWidget):
    """Paints the latest frame, letterboxed, or a status message."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setMinimumHeight(80)
        self._image: QImage | None = None
        self._message = "Not started"

    def set_image(self, img: QImage) -> None:
        self._image = img
        self.update()

    def set_message(self, text: str) -> None:
        self._message = text
        if self._image is None:
            self.update()

    def clear(self) -> None:
        self._image = None
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#08080b"))
        if self._image is not None and not self._image.isNull():
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            scaled = self._image.scaled(
                self.size(), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            p.drawImage((self.width() - scaled.width()) // 2,
                        (self.height() - scaled.height()) // 2, scaled)
        else:
            p.setPen(QPen(QColor("#4a4f63")))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       self._message)
        p.end()


# ==========================================================================
# Camera tile
# ==========================================================================

class CameraTile(QFrame):
    """One live camera: video, a title bar and per-camera controls."""

    maximize_requested = Signal(object)
    audio_requested = Signal(object, bool)

    def __init__(self, camera: Camera, cfg: Config, parent=None) -> None:
        super().__init__(parent)
        self.camera = camera
        self.cfg = cfg
        self.worker: StreamWorker | None = None
        self.recorder: Recorder | None = None
        self._state = STOPPED
        self._msg = "not started"
        self._fps = 0.0
        self._mbps = 0.0

        self.setMinimumSize(200, 150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        self.setStyleSheet(
            "CameraTile { background:#08080b; border:1px solid #23242e;"
            " border-radius:8px; }")

        root = QVBoxLayout(self)
        root.setContentsMargins(1, 1, 1, 1)
        root.setSpacing(0)

        # --- title bar
        bar = QWidget()
        bar.setStyleSheet("background:#12131a;")
        bar.setFixedHeight(30)
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(10, 0, 10, 0)
        bl.setSpacing(7)
        self.dot = QLabel("●")
        self.dot.setStyleSheet("color:#55586a; font-size:13px;")
        self.lbl_name = QLabel(camera.name)
        self.lbl_name.setStyleSheet("font-weight:600; color:#e8eaf0;")
        self.lbl_stat = QLabel("")
        self.lbl_stat.setObjectName("status")
        bl.addWidget(self.dot)
        bl.addWidget(self.lbl_name)
        bl.addStretch(1)
        bl.addWidget(self.lbl_stat)
        root.addWidget(bar)

        # --- video
        self.surface = VideoSurface()
        root.addWidget(self.surface, 1)

        # --- controls
        ctl = QWidget()
        ctl.setStyleSheet("background:#12131a;")
        ctl.setFixedHeight(32)
        cl = QHBoxLayout(ctl)
        cl.setContentsMargins(7, 3, 7, 3)
        cl.setSpacing(5)
        self.btn_snap = self._mini("Snapshot", self.take_snapshot)
        self.btn_rec = self._mini("Record", self.toggle_record, checkable=True)
        self.btn_audio = self._mini("Audio", self._audio_clicked,
                                    checkable=True)
        self.btn_reload = self._mini("Reconnect", self.restart)
        self.btn_max = self._mini("Expand",
                                  lambda: self.maximize_requested.emit(self))
        for b in (self.btn_snap, self.btn_rec, self.btn_audio,
                  self.btn_reload):
            cl.addWidget(b)
        cl.addStretch(1)
        cl.addWidget(self.btn_max)
        root.addWidget(ctl)

    def _mini(self, text: str, slot, checkable: bool = False) -> QPushButton:
        b = QPushButton(text)
        b.setObjectName("tile")
        b.setCheckable(checkable)
        b.clicked.connect(slot)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        return b

    # -- streaming --------------------------------------------------------

    def start(self) -> None:
        if self.worker is not None:
            return
        h = self.cfg.decode_height
        self.worker = StreamWorker(
            self.camera.stream_url(self.cfg), int(h * 16 / 9), h,
            self.camera.transport, self.cfg.hw_accel, self.cfg.reconnect_secs)
        self.worker.frame_ready.connect(self.surface.set_image)
        self.worker.state_changed.connect(self._on_state)
        self.worker.stats_ready.connect(self._on_stats)
        self.worker.start()

    def stop(self) -> None:
        if self.recorder is not None and self.recorder.running:
            self.recorder.stop()
        self.recorder = None
        self.btn_rec.setChecked(False)
        self.btn_rec.setText("Record")
        if self.worker is not None:
            self.worker.stop()
            self.worker.wait(5000)
            self.worker = None
        self._state = STOPPED
        self._msg = "stopped"
        self.surface.clear()
        self.surface.set_message("Stopped")
        self._refresh_labels()

    def restart(self) -> None:
        self.stop()
        self.start()

    def set_quality(self, height: int) -> None:
        if self.worker is not None:
            self.worker.set_size(int(height * 16 / 9), height)

    # -- slots ------------------------------------------------------------

    def _on_state(self, state: str, msg: str) -> None:
        self._state = state
        self._msg = msg
        self._refresh_labels()
        if state == CONNECTING:
            self.surface.set_message("Connecting…")
        elif state == RETRYING:
            self.surface.set_message(msg)

    def _on_stats(self, fps: float, mbps: float) -> None:
        self._fps, self._mbps = fps, mbps
        self._refresh_labels()

    def _refresh_labels(self) -> None:
        colors = {LIVE: "#3fb950", RETRYING: "#d29922", CONNECTING: "#58a6ff",
                  STOPPED: "#55586a"}
        self.dot.setStyleSheet("color:%s; font-size:13px;"
                               % colors.get(self._state, "#8b949e"))
        if self._state == LIVE:
            self.lbl_stat.setText("%.0f fps   %.1f Mb/s"
                                  % (self._fps, self._mbps))
        else:
            self.lbl_stat.setText(self._msg[:56])

    # -- actions ----------------------------------------------------------

    def _audio_clicked(self) -> None:
        self.audio_requested.emit(self, self.btn_audio.isChecked())

    def take_snapshot(self) -> None:
        img = self.worker.snapshot() if self.worker is not None else None
        if img is None:
            QMessageBox.information(self, APP_NAME, "No frame to save yet.")
            return
        ensure_dirs(self.cfg)
        safe = "".join(c if c.isalnum() or c in "-_ " else "_"
                       for c in self.camera.name).strip() or "camera"
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        path = os.path.join(self.cfg.snapshot_dir, "%s_%s.png" % (safe, stamp))
        if img.save(path):
            self.lbl_stat.setText("snapshot saved")
            QTimer.singleShot(2500, self._refresh_labels)
        else:
            QMessageBox.warning(self, APP_NAME, "Could not save %s" % path)

    def toggle_record(self) -> None:
        if self.recorder is not None and self.recorder.running:
            path = self.recorder.stop()
            self.recorder = None
            self.btn_rec.setText("Record")
            self.btn_rec.setChecked(False)
            QMessageBox.information(self, APP_NAME,
                                    "Saved recording:\n%s" % path)
            return
        ensure_dirs(self.cfg)
        self.recorder = Recorder(self.camera.stream_url(self.cfg),
                                 self.cfg.recording_dir,
                                 self.camera.name, self.camera.transport)
        if self.recorder.start():
            self.btn_rec.setText("Stop rec")
            self.btn_rec.setChecked(True)
        else:
            self.recorder = None
            self.btn_rec.setChecked(False)
            QMessageBox.warning(self, APP_NAME, "Could not start recording.")

    def mouseDoubleClickEvent(self, event) -> None:
        self.maximize_requested.emit(self)


# ==========================================================================
# Scan worker
# ==========================================================================

class ScanWorker(QThread):
    line = Signal(str)
    finished_scan = Signal(object)   # list[core.ProbeResult]

    def __init__(self, ip: str, user: str, pw: str, channels: int,
                 timeout: float, parent=None) -> None:
        super().__init__(parent)
        self.ip, self.user, self.pw = ip, user, pw
        self.channels = channels
        self.timeout = timeout
        self.stop_flag = threading.Event()

    def cancel(self) -> None:
        self.stop_flag.set()

    def run(self) -> None:
        log = self.line.emit
        results: list = []
        try:
            log("=" * 64)
            log(" Step 1  -  what is answering at %s ?" % self.ip)
            log("=" * 64)
            info = core.fingerprint(self.ip, log)
            if self.stop_flag.is_set():
                self.finished_scan.emit([])
                return

            log("")
            log("=" * 64)
            log(" Step 2  -  hunting for a working RTSP path")
            log("=" * 64)
            rtsp_ports = [p for p in info["open"]
                          if p in (554, 555, 8554, 10554)]
            if not rtsp_ports:
                # No standard RTSP port. Rather than give up, try any other
                # port that is actually listening and isn't plainly a web or
                # vendor-SDK port - some recorders park RTSP somewhere odd.
                skip = (80, 443, 8080, 8000, 88, 9000, 5000, 2000,
                        34567, 37777, 8899)
                rtsp_ports = [p for p in info["open"] if p not in skip]
                if rtsp_ports:
                    log("No standard RTSP port is open; trying the other "
                        "open port(s) instead: %s"
                        % ", ".join(str(p) for p in rtsp_ports))
            found = core.discover_streams(
                self.ip, self.user, self.pw, channels=[1], ports=rtsp_ports,
                timeout=self.timeout, stop_flag=self.stop_flag, log=log)

            if found and not self.stop_flag.is_set():
                log("")
                log("=" * 64)
                log(" Step 3  -  which channels actually have a camera?")
                log("=" * 64)
                results = core.expand_channels(
                    found, self.ip, self.user, self.pw,
                    max_channels=self.channels, timeout=self.timeout,
                    log=log, stop_flag=self.stop_flag)
            else:
                results = found
        except Exception as exc:                      # keep the UI alive
            log("\nScan crashed: %r" % exc)
        self.finished_scan.emit(results)


# ==========================================================================
# Setup page
# ==========================================================================

class SetupPage(QWidget):
    cameras_changed = Signal()
    go_live = Signal()

    def __init__(self, cfg: Config, parent=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.scan: ScanWorker | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        title = QLabel("DVR setup")
        title.setObjectName("h1")
        root.addWidget(title)
        hint = QLabel("Enter the recorder's address and login, then let the "
                      "app find the streams. It tries every known RTSP "
                      "layout and reports exactly what each one answered.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        root.addWidget(hint)

        split = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(split, 1)

        # ---- left: connection + cameras ---------------------------------
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 6, 10, 0)
        ll.setSpacing(12)

        box = QGroupBox("Connection")
        form = QFormLayout(box)
        form.setSpacing(8)
        self.ed_ip = QLineEdit(cfg.ip)
        self.ed_ip.setPlaceholderText("192.168.1.108")
        self.ed_user = QLineEdit(cfg.username)
        self.ed_user.setPlaceholderText("admin")
        self.ed_pw = QLineEdit(cfg.password)
        self.ed_pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.ed_pw.setPlaceholderText("DVR password")
        self.sp_ch = QSpinBox()
        self.sp_ch.setRange(1, 64)
        self.sp_ch.setValue(cfg.channels)
        self.sp_to = QSpinBox()
        self.sp_to.setRange(2, 30)
        self.sp_to.setValue(int(cfg.probe_timeout))
        self.sp_to.setSuffix(" s")
        form.addRow("IP address", self.ed_ip)
        form.addRow("Username", self.ed_user)
        form.addRow("Password", self.ed_pw)
        form.addRow("Channels on DVR", self.sp_ch)
        form.addRow("Probe timeout", self.sp_to)
        ll.addWidget(box)

        btns = QHBoxLayout()
        self.btn_scan = QPushButton("Find my cameras")
        self.btn_scan.setObjectName("accent")
        self.btn_scan.clicked.connect(self.start_scan)
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_scan)
        self.btn_web = QPushButton("Open web UI")
        self.btn_web.clicked.connect(self.open_web)
        btns.addWidget(self.btn_scan, 1)
        btns.addWidget(self.btn_stop)
        btns.addWidget(self.btn_web)
        ll.addLayout(btns)

        cam_box = QGroupBox("Cameras")
        cbl = QVBoxLayout(cam_box)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["On", "Name", "Stream URL",
                                              "Transport"])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.itemChanged.connect(self._table_edited)
        cbl.addWidget(self.table, 1)

        row = QHBoxLayout()
        for text, slot in (("Add manually", self.add_manual),
                           ("Test", self.test_selected),
                           ("Preview", self.preview_selected),
                           ("Copy URL", self.copy_selected),
                           ("Remove", self.remove_selected)):
            b = QPushButton(text)
            if text == "Remove":
                b.setObjectName("danger")
            b.clicked.connect(slot)
            row.addWidget(b)
        cbl.addLayout(row)
        ll.addWidget(cam_box, 1)

        go = QPushButton("Save and view cameras")
        go.setObjectName("accent")
        go.clicked.connect(self._save_and_go)
        ll.addWidget(go)
        split.addWidget(left)

        # ---- right: log --------------------------------------------------
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(10, 6, 0, 0)
        rl.setSpacing(8)
        head = QHBoxLayout()
        lbl = QLabel("Diagnostics")
        lbl.setStyleSheet("font-weight:600; color:#8f94a6;")
        head.addWidget(lbl)
        head.addStretch(1)
        b_save = QPushButton("Save log")
        b_save.clicked.connect(self.save_log)
        b_clear = QPushButton("Clear")
        b_clear.clicked.connect(self.log_view_clear)
        head.addWidget(b_save)
        head.addWidget(b_clear)
        rl.addLayout(head)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        self.log_view.setPlaceholderText(
            "Press 'Find my cameras' and the whole conversation with your "
            "DVR shows up here: which ports are open, what the device calls "
            "itself, and what every RTSP path replied.")
        rl.addWidget(self.log_view, 1)
        split.addWidget(right)
        split.setSizes([560, 620])

        self.reload_table()

    def log_view_clear(self) -> None:
        self.log_view.clear()

    # -- config helpers ---------------------------------------------------

    def pull_config(self) -> None:
        self.cfg.ip = self.ed_ip.text().strip()
        self.cfg.username = self.ed_user.text().strip()
        self.cfg.password = self.ed_pw.text()
        self.cfg.channels = self.sp_ch.value()
        self.cfg.probe_timeout = float(self.sp_to.value())

    def log(self, text: str) -> None:
        self.log_view.appendPlainText(text)

    # -- scanning ---------------------------------------------------------

    def start_scan(self) -> None:
        self.pull_config()
        if not self.cfg.ip:
            QMessageBox.warning(self, APP_NAME,
                                "Enter the DVR's IP address first.")
            return
        if not core.ffmpeg_available():
            QMessageBox.critical(
                self, APP_NAME,
                "ffmpeg was not found.\n\nInstall it with:\n"
                "    winget install Gyan.FFmpeg")
            return
        self.cfg.save()
        self.log_view.clear()
        self.btn_scan.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.scan = ScanWorker(self.cfg.ip, self.cfg.username,
                               self.cfg.password, self.cfg.channels,
                               self.cfg.probe_timeout)
        self.scan.line.connect(self.log)
        self.scan.finished_scan.connect(self.scan_done)
        self.scan.start()

    def stop_scan(self) -> None:
        if self.scan is not None:
            self.scan.cancel()
            self.log("\nCancelling - waiting for probes already in flight...")
        self.btn_stop.setEnabled(False)

    def scan_done(self, results: list) -> None:
        self.btn_scan.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.scan = None
        if not results:
            self.log("\nNothing to add. The notes above say whether it was "
                     "the password, the path, or RTSP being switched off on "
                     "the recorder.")
            return
        existing = {c.url for c in self.cfg.cameras}
        added = 0
        for r in results:
            cam = Camera.from_url("Camera %d" % r.channel, r.url,
                                  channel=r.channel, transport=r.transport,
                                  cfg=self.cfg)
            if cam.url in existing:
                continue
            existing.add(cam.url)
            self.cfg.cameras.append(cam)
            added += 1
        self.reload_table()
        self.cfg.save()
        self.log("\nAdded %d camera(s). Press 'Save and view cameras'."
                 % added)

    def save_log(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save diagnostics log",
            os.path.join(APP_DIR, "dvr_scan_log.txt"), "Text files (*.txt)")
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self.log_view.toPlainText())

    def open_web(self) -> None:
        ip = self.ed_ip.text().strip()
        if ip:
            webbrowser.open("http://%s/" % ip)

    # -- camera table -----------------------------------------------------

    def reload_table(self) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for cam in self.cfg.cameras:
            r = self.table.rowCount()
            self.table.insertRow(r)

            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable
                         | Qt.ItemFlag.ItemIsEnabled)
            chk.setCheckState(Qt.CheckState.Checked if cam.enabled
                              else Qt.CheckState.Unchecked)
            self.table.setItem(r, 0, chk)
            self.table.setItem(r, 1, QTableWidgetItem(cam.name))

            url_item = QTableWidgetItem(core.redact(cam.url))
            url_item.setFlags(Qt.ItemFlag.ItemIsSelectable
                              | Qt.ItemFlag.ItemIsEnabled)
            url_item.setForeground(QColor("#7d8296"))
            self.table.setItem(r, 2, url_item)

            t_item = QTableWidgetItem(cam.transport.upper())
            t_item.setFlags(Qt.ItemFlag.ItemIsSelectable
                            | Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(r, 3, t_item)
        self.table.blockSignals(False)

    def _table_edited(self, item: QTableWidgetItem) -> None:
        r = item.row()
        if r >= len(self.cfg.cameras):
            return
        if item.column() == 0:
            self.cfg.cameras[r].enabled = (
                item.checkState() == Qt.CheckState.Checked)
        elif item.column() == 1:
            self.cfg.cameras[r].name = item.text().strip() or "Camera"
        self.cfg.save()

    def _selected(self) -> int:
        rows = {i.row() for i in self.table.selectedIndexes()}
        if not rows:
            QMessageBox.information(self, APP_NAME,
                                    "Select a camera in the list first.")
            return -1
        return min(rows)

    def add_manual(self) -> None:
        url, ok = QInputDialog.getText(
            self, "Add camera",
            "Full RTSP URL (include the username and password):",
            QLineEdit.EchoMode.Normal,
            "rtsp://admin:password@%s:554/"
            % (self.ed_ip.text().strip() or "192.168.1.108"))
        if not ok or not url.strip():
            return
        self.cfg.cameras.append(Camera.from_url(
            "Camera %d" % (len(self.cfg.cameras) + 1), url.strip(),
            cfg=self.cfg))
        self.reload_table()
        self.cfg.save()

    def test_selected(self) -> None:
        r = self._selected()
        if r < 0:
            return
        cam = self.cfg.cameras[r]
        url = cam.stream_url(self.cfg)
        self.log("\nTesting %s ..." % core.redact(url))
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            res = core.probe_url(url, self.cfg.probe_timeout, cam.transport)
            if not res.works and cam.transport == "tcp":
                self.log("  TCP: %s - retrying over UDP..." % res.label)
                res = core.probe_url(url, self.cfg.probe_timeout, "udp")
        finally:
            QApplication.restoreOverrideCursor()
        self.log("  %s  -  %s" % (res.label, res.summary()))
        if res.works and res.transport != cam.transport:
            cam.transport = res.transport
            self.reload_table()
            self.cfg.save()

    def preview_selected(self) -> None:
        """Open the stream in ffplay - proves it works outside this app."""
        r = self._selected()
        if r < 0:
            return
        cam = self.cfg.cameras[r]
        ffplay = core.FFMPEG.replace("ffmpeg.EXE", "ffplay.exe") \
                            .replace("ffmpeg.exe", "ffplay.exe")
        try:
            subprocess.Popen([ffplay, "-hide_banner",
                              "-rtsp_transport", cam.transport,
                              "-fflags", "nobuffer",
                              "-window_title", cam.name,
                              cam.stream_url(self.cfg)])
        except OSError as exc:
            QMessageBox.warning(self, APP_NAME,
                                "Could not run ffplay: %s" % exc)

    def copy_selected(self) -> None:
        r = self._selected()
        if r < 0:
            return
        QGuiApplication.clipboard().setText(
            self.cfg.cameras[r].stream_url(self.cfg))
        self.log("\nFull URL (including the password) copied to clipboard.")

    def remove_selected(self) -> None:
        r = self._selected()
        if r < 0:
            return
        del self.cfg.cameras[r]
        self.reload_table()
        self.cfg.save()

    def _save_and_go(self) -> None:
        self.pull_config()
        self.cfg.save()
        self.cameras_changed.emit()
        self.go_live.emit()


# ==========================================================================
# Live page
# ==========================================================================

class LivePage(QWidget):
    def __init__(self, cfg: Config, audio: AudioPlayer, parent=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.audio = audio
        self.tiles: list[CameraTile] = []
        self.maximized: CameraTile | None = None
        self._signature: tuple = ()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bar = QFrame()
        bar.setObjectName("toolbar")
        bar.setFixedHeight(48)
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(14, 7, 14, 7)
        bl.setSpacing(8)

        self.btn_start = QPushButton("Connect all")
        self.btn_start.setObjectName("accent")
        self.btn_start.clicked.connect(self.start_all)
        self.btn_stop = QPushButton("Stop all")
        self.btn_stop.clicked.connect(self.stop_all)

        self.cmb_grid = QComboBox()
        self.cmb_grid.addItems(list(GRID_COLS.keys()))
        self.cmb_grid.setCurrentText(
            cfg.grid if cfg.grid in GRID_COLS else "2x2")
        self.cmb_grid.currentTextChanged.connect(self.set_grid)

        self.cmb_q = QComboBox()
        self.cmb_q.addItems(list(QUALITY.keys()))
        for k, v in QUALITY.items():
            if v == cfg.decode_height:
                self.cmb_q.setCurrentText(k)
        self.cmb_q.currentTextChanged.connect(self.set_quality)

        self.lbl_info = QLabel("")
        self.lbl_info.setObjectName("pill")

        bl.addWidget(self.btn_start)
        bl.addWidget(self.btn_stop)
        bl.addSpacing(12)
        bl.addWidget(QLabel("Layout"))
        bl.addWidget(self.cmb_grid)
        bl.addSpacing(6)
        bl.addWidget(QLabel("Quality"))
        bl.addWidget(self.cmb_q)
        bl.addStretch(1)
        bl.addWidget(self.lbl_info)
        root.addWidget(bar)

        self.canvas = QWidget()
        self.grid = QGridLayout(self.canvas)
        self.grid.setContentsMargins(8, 8, 8, 8)
        self.grid.setSpacing(8)
        root.addWidget(self.canvas, 1)

        self.empty = QLabel("No cameras yet.\n\n"
                            "Open Setup and press 'Find my cameras'.")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setObjectName("hint")

    # -- tiles ------------------------------------------------------------

    def _config_signature(self) -> tuple:
        return tuple((c.name, c.url, c.transport, c.cred_enc,
                      self.cfg.username, self.cfg.password_enc)
                     for c in self.cfg.cameras if c.enabled and c.url)

    def sync_from_config(self) -> bool:
        """Rebuild tiles only if the camera list actually changed."""
        sig = self._config_signature()
        if sig == self._signature and self.tiles:
            return False
        self._signature = sig
        self.rebuild()
        return True

    def rebuild(self) -> None:
        self.stop_all()
        self.audio.stop()
        for t in self.tiles:
            t.setParent(None)
            t.deleteLater()
        self.tiles = []
        self.maximized = None

        cams = [c for c in self.cfg.cameras if c.enabled and c.url]
        for cam in cams:
            tile = CameraTile(cam, self.cfg)
            tile.maximize_requested.connect(self.toggle_max)
            tile.audio_requested.connect(self.set_audio)
            self.tiles.append(tile)

        self._signature = self._config_signature()
        self.layout_tiles()
        self.lbl_info.setText(
            "%d camera%s" % (len(cams), "" if len(cams) == 1 else "s")
            if cams else "")

    def layout_tiles(self) -> None:
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w is not None:
                w.setParent(None)

        if not self.tiles:
            self.empty.setParent(self.canvas)
            self.grid.addWidget(self.empty, 0, 0)
            self.empty.setVisible(True)
            return
        self.empty.setVisible(False)

        if self.maximized is not None:
            self.grid.addWidget(self.maximized, 0, 0)
            for t in self.tiles:
                t.setVisible(t is self.maximized)
            return

        cols = GRID_COLS.get(self.cmb_grid.currentText(), 2)
        for i, t in enumerate(self.tiles):
            t.setVisible(True)
            self.grid.addWidget(t, i // cols, i % cols)

    def set_grid(self, text: str) -> None:
        self.cfg.grid = text
        self.cfg.save()
        self.maximized = None
        for t in self.tiles:
            t.btn_max.setText("Expand")
        self.layout_tiles()

    def set_quality(self, text: str) -> None:
        h = QUALITY.get(text, 480)
        self.cfg.decode_height = h
        self.cfg.save()
        for t in self.tiles:
            t.set_quality(h)

    def toggle_max(self, tile: CameraTile) -> None:
        self.maximized = None if self.maximized is tile else tile
        for t in self.tiles:
            t.btn_max.setText("Collapse" if t is self.maximized else "Expand")
        self.layout_tiles()

    def set_audio(self, tile: CameraTile, on: bool) -> None:
        self.audio.stop()
        for t in self.tiles:
            if t is not tile:
                t.btn_audio.setChecked(False)
        if on and not self.audio.play(tile.camera.stream_url(self.cfg),
                                      tile.camera.transport):
            tile.btn_audio.setChecked(False)
            QMessageBox.warning(self, APP_NAME, "Could not start audio.")

    # -- control ----------------------------------------------------------

    def start_all(self) -> None:
        for t in self.tiles:
            t.start()

    def stop_all(self) -> None:
        for t in self.tiles:
            t.stop()


# ==========================================================================
# Main window
# ==========================================================================

class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.cfg = Config.load()
        ensure_dirs(self.cfg)
        self.audio = AudioPlayer()

        self.setWindowTitle(APP_NAME)
        self.resize(1280, 820)
        self.setMinimumSize(900, 560)

        self.stack = QStackedWidget()
        self.live = LivePage(self.cfg, self.audio)
        self.setup = SetupPage(self.cfg)
        self.stack.addWidget(self.live)
        self.stack.addWidget(self.setup)

        wrap = QWidget()
        wl = QVBoxLayout(wrap)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(0)

        nav = QFrame()
        nav.setObjectName("toolbar")
        nav.setFixedHeight(46)
        nl = QHBoxLayout(nav)
        nl.setContentsMargins(14, 6, 14, 6)
        nl.setSpacing(4)
        brand = QLabel("DVR Viewer")
        brand.setStyleSheet("font-size:15px; font-weight:700; color:#eef0f5;")
        self.btn_live = QPushButton("Live")
        self.btn_live.setObjectName("nav")
        self.btn_live.setCheckable(True)
        self.btn_live.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        self.btn_setup = QPushButton("Setup")
        self.btn_setup.setObjectName("nav")
        self.btn_setup.setCheckable(True)
        self.btn_setup.clicked.connect(lambda: self.stack.setCurrentIndex(1))
        self.lbl_host = QLabel("")
        self.lbl_host.setObjectName("pill")
        nl.addWidget(brand)
        nl.addSpacing(16)
        nl.addWidget(self.btn_live)
        nl.addWidget(self.btn_setup)
        nl.addStretch(1)
        nl.addWidget(self.lbl_host)
        wl.addWidget(nav)
        wl.addWidget(self.stack, 1)
        self.setCentralWidget(wrap)

        self.setup.cameras_changed.connect(self._cameras_changed)
        self.setup.go_live.connect(lambda: self.stack.setCurrentIndex(0))
        self.stack.currentChanged.connect(self._page_changed)

        act_full = QAction("Fullscreen", self)
        act_full.setShortcut(QKeySequence("F11"))
        act_full.triggered.connect(self.toggle_fullscreen)
        self.addAction(act_full)
        act_esc = QAction("Leave fullscreen", self)
        act_esc.setShortcut(QKeySequence("Esc"))
        act_esc.triggered.connect(self._escape)
        self.addAction(act_esc)

        self.live.sync_from_config()
        self._refresh_chrome()
        if not self.cfg.cameras:
            self.stack.setCurrentIndex(1)
        else:
            QTimer.singleShot(350, self.live.start_all)

    def _cameras_changed(self) -> None:
        if self.live.sync_from_config():
            QTimer.singleShot(200, self.live.start_all)

    def _refresh_chrome(self) -> None:
        self.lbl_host.setText(self.cfg.ip or "no DVR configured")
        idx = self.stack.currentIndex()
        self.btn_live.setChecked(idx == 0)
        self.btn_setup.setChecked(idx == 1)

    def _page_changed(self, idx: int) -> None:
        self._refresh_chrome()
        if idx == 0 and self.live.sync_from_config():
            QTimer.singleShot(200, self.live.start_all)

    def _escape(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        elif self.live.maximized is not None:
            self.live.toggle_max(self.live.maximized)

    def toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def closeEvent(self, event) -> None:
        self.audio.stop()
        self.live.stop_all()
        if self.setup.scan is not None:
            self.setup.scan.cancel()
            self.setup.scan.wait(4000)
        self.cfg.save()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyleSheet(DARK_QSS)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
