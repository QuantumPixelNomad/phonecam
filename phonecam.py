#!/usr/bin/env python3
"""Phone Cam - use an Android phone's camera and mic as a Linux webcam/mic.

A small Qt front end for scrcpy: pick the phone, pick the camera
(main, ultra-wide, front, ...), press Start. The camera appears as
"Phone Cam" and the microphone as "PhoneMic" in any app.
"""

import math
import os
import re
import shutil
import subprocess
import sys
import threading

from PySide6.QtCore import QObject, QProcess, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton,
    QVBoxLayout, QWidget,
)

APP_NAME = "Phone Cam"
VIDEO_NR = 10
VIDEO_DEV = f"/dev/video{VIDEO_NR}"
CAM_LABEL = "Phone Cam"
SINK = "phonemic"
SOURCE = "phonemic_in"


# --------------------------------------------------------------- helpers

def run(cmd, timeout=15):
    """Run a command, return (exit code, combined output)."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return 127, f"{cmd[0]} not found"
    except subprocess.TimeoutExpired:
        return 124, "timed out"


def list_devices():
    """Connected phones, one entry per physical phone.

    The same phone can be listed twice over Wi-Fi (once from pairing,
    once from `adb connect`), so entries are merged by serial number.
    """
    _, out = run(["adb", "devices", "-l"])
    phones, others = {}, []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        if state != "device":
            others.append({"serial": serial, "state": state,
                           "label": f"{serial} ({state})"})
            continue
        _, props = run(["adb", "-s", serial, "shell",
                        "getprop ro.serialno; getprop ro.product.marketname; "
                        "getprop ro.product.model; getprop ro.build.version.release"], 10)
        p = (props.splitlines() + ["", "", "", ""])[:4]
        hw, market, model, android = (x.strip() for x in p)
        if ":" in serial or "._adb-tls" in serial:
            link, rank = "Wi-Fi", (1 if re.match(r"^[\d.]+:\d+$", serial) else 2)
        else:
            link, rank = "USB", 0
        key = hw or serial
        name = market or model or serial
        entry = {"serial": serial, "state": "device", "rank": rank, "key": key,
                 "label": f"{name} - {link}" + (f" - Android {android}" if android else "")}
        if key not in phones or rank < phones[key]["rank"]:
            phones[key] = entry
    return list(phones.values()) + others


def focal_info(serial):
    """35 mm-equivalent focal length per camera id, read from dumpsys."""
    _, out = run(["adb", "-s", serial, "shell", "dumpsys", "media.camera"], 20)
    info, cam, want = {}, None, None
    for line in out.splitlines():
        m = re.match(r"^== Camera HAL device (\S+).*static information", line)
        if m:
            idm = re.search(r"/(\d+)$", m.group(1))
            cam = idm.group(1) if idm else None
            if cam is not None:
                info[cam] = {}
            continue
        if cam is None:
            continue
        if "lens.info.availableFocalLengths" in line:
            want = "f"
        elif "sensor.info.physicalSize" in line:
            want = "size"
        elif want:
            nums = [float(x) for x in re.findall(r"[\d.]+", line)]
            if nums:
                info[cam][want] = nums
            want = None
    eq = {}
    for cid, d in info.items():
        if d.get("f") and d.get("size") and len(d["size"]) >= 2:
            diag = math.hypot(d["size"][0], d["size"][1])
            if diag > 0:
                eq[cid] = d["f"][0] * 43.27 / diag
    return eq


def list_cameras(serial):
    """Cameras reported by scrcpy, with friendly names."""
    _, out = run(["scrcpy", "-s", serial, "--list-cameras"], 30)
    cams = []
    for m in re.finditer(r"--camera-id=(\S+)\s+\((\w+), (\d+)x(\d+), fps=\{([^}]*)\}"
                         r"(?:, zoom-range=\[([\d.]+), ([\d.]+)\])?", out):
        cid, facing, w, h, fps, zmin, zmax = m.groups()
        cams.append({"id": cid, "facing": facing, "w": int(w), "h": int(h),
                     "fps": [int(x) for x in re.findall(r"\d+", fps)],
                     "zoom": float(zmax) if zmax else 1.0})
    if not cams:
        err = next((l for l in out.splitlines() if "ERROR" in l), out.strip()[-200:])
        raise RuntimeError(err or "No cameras found")

    eq = focal_info(serial)
    backs = [c for c in cams if c["facing"] == "back"]
    main = max(backs, key=lambda c: c["w"] * c["h"], default=None)
    main_eq = eq.get(main["id"]) if main else None
    for c in cams:
        mp = c["w"] * c["h"] / 1e6
        e = eq.get(c["id"])
        if c["facing"] == "front":
            kind = "Front"
        elif c["facing"] == "external":
            kind = "External"
        elif e and e < 20:
            kind = "Ultra-wide"
        elif e and main_eq and e > main_eq * 1.6:
            kind = f"Telephoto {e / main_eq:.1f}x"
        elif main and c is not main and mp < 5:
            kind = "Macro / depth"
        else:
            kind = "Main" if c["facing"] == "back" else c["facing"].title()
        extra = f" - {e:.0f} mm" if e else ""
        c["label"] = f"{kind} - {mp:.0f} MP{extra}  (id {c['id']})"
    # Phones often expose the same lens several times; keep the first of each.
    seen, unique = set(), []
    for c in cams:
        sig = (c["facing"], c["w"], c["h"], round(eq.get(c["id"], 0)))
        if sig not in seen:
            seen.add(sig)
            unique.append(c)
    cams = unique
    order = {"Main": 0, "Ultra-wide": 1, "Telephoto": 2, "Front": 3}
    cams.sort(key=lambda c: (order.get(c["label"].split(" ")[0], 4), int(c["id"]) if c["id"].isdigit() else 99))
    return cams


def ensure_virtual_mic():
    _, sinks = run(["pactl", "list", "short", "sinks"])
    if not any(l.split("\t")[1:2] == [SINK] for l in sinks.splitlines()):
        run(["pactl", "load-module", "module-null-sink", f"sink_name={SINK}",
             "sink_properties=device.description=PhoneMicSink"])
    _, sources = run(["pactl", "list", "short", "sources"])
    if not any(l.split("\t")[1:2] == [SOURCE] for l in sources.splitlines()):
        run(["pactl", "load-module", "module-remap-source", f"master={SINK}.monitor",
             f"source_name={SOURCE}", "source_properties=device.description=PhoneMic"])


def route_scrcpy_audio():
    """Move scrcpy's playback stream into the PhoneMic sink."""
    _, out = run(["pactl", "list", "sink-inputs"], 5)
    _, sinks = run(["pactl", "list", "short", "sinks"], 5)
    target = next((l.split("\t")[0] for l in sinks.splitlines()
                   if l.split("\t")[1:2] == [SINK]), None)
    if not target:
        return
    sid = sink = None
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("Sink Input #"):
            sid, sink = s.split("#")[1], None
        elif s.startswith("Sink:"):
            sink = s.split(":", 1)[1].strip()
        elif s == 'application.name = "scrcpy"' and sid and sink != target:
            run(["pactl", "move-sink-input", sid, SINK], 5)


def virtual_camera_ready():
    try:
        with open(f"/sys/devices/virtual/video4linux/video{VIDEO_NR}/name") as f:
            return f.read().strip() == CAM_LABEL
    except OSError:
        return False


# --------------------------------------------------------------- GUI

class Bridge(QObject):
    devices = Signal(list)
    cameras = Signal(str, object)   # serial, list or Exception
    message = Signal(str)


def in_thread(fn):
    threading.Thread(target=fn, daemon=True).start()


class Window(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(QIcon.fromTheme("camera-web"))
        self.settings = QSettings("phonecam", "phonecam")
        self.bridge = Bridge()
        self.bridge.devices.connect(self.on_devices)
        self.bridge.cameras.connect(self.on_cameras)
        self.bridge.message.connect(self.set_status)
        self.proc = None
        self.cams = []

        self.device = QComboBox()
        self.device.currentIndexChanged.connect(self.on_device_changed)
        refresh = QPushButton(QIcon.fromTheme("view-refresh"), "")
        refresh.setToolTip("Refresh phones")
        refresh.clicked.connect(self.refresh_devices)
        drow = QHBoxLayout()
        drow.addWidget(self.device, 1)
        drow.addWidget(refresh)

        self.addr = QLineEdit(self.settings.value("addr", ""))
        self.addr.setPlaceholderText("IP:port from Wireless debugging")
        connect = QPushButton("Connect")
        connect.clicked.connect(self.wifi_connect)
        pair = QPushButton("Pair...")
        pair.setToolTip("First time only: pair with a code (Wireless debugging > Pair device with pairing code)")
        pair.clicked.connect(self.wifi_pair)
        wrow = QHBoxLayout()
        wrow.addWidget(self.addr, 1)
        wrow.addWidget(connect)
        wrow.addWidget(pair)

        self.camera = QComboBox()
        self.camera.currentIndexChanged.connect(self.on_camera_changed)

        self.quality = QComboBox()
        self.quality.addItems(["720p", "1080p", "480p"])
        self.quality.setCurrentText(self.settings.value("quality", "720p"))

        self.rotate = QComboBox()
        self.rotate.addItems(["0°", "90°", "180°", "270°"])
        self.rotate.setCurrentText(self.settings.value("rotate", "0°"))

        self.fps = QComboBox()

        self.zoom = QDoubleSpinBox()
        self.zoom.setRange(1.0, 1.0)
        self.zoom.setSingleStep(0.5)
        self.zoom.setSuffix("x")

        self.mic = QCheckBox("Use the phone's microphone (PhoneMic)")
        self.mic.setChecked(self.settings.value("mic", "true") == "true")

        form = QFormLayout()
        form.addRow("Phone", drow)
        form.addRow("Wi-Fi", wrow)
        form.addRow("Camera", self.camera)
        form.addRow("Quality", self.quality)
        form.addRow("FPS", self.fps)
        form.addRow("Rotate", self.rotate)
        form.addRow("Zoom", self.zoom)
        form.addRow("", self.mic)

        self.start = QPushButton(QIcon.fromTheme("media-playback-start"), "Start")
        self.start.setMinimumHeight(36)
        self.start.clicked.connect(self.toggle)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)

        hint = QLabel(f'In Discord, Zoom, Meet...: camera <b>{CAM_LABEL}</b>, microphone <b>PhoneMic</b>.')
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(placeholder-text);")

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.start)
        lay.addWidget(self.status)
        lay.addWidget(hint)

        self.router = QTimer(self)
        self.router.timeout.connect(lambda: in_thread(route_scrcpy_audio))

        self.resize(460, 0)
        self.update_controls()
        self.refresh_devices()

    # ---- status / state
    def set_status(self, text):
        self.status.setText(text)

    def running(self):
        return self.proc is not None and self.proc.state() != QProcess.NotRunning

    def update_controls(self):
        busy = self.running()
        for w in (self.device, self.camera, self.quality, self.fps, self.rotate, self.zoom, self.mic):
            w.setEnabled(not busy)
        self.camera.setEnabled(not busy and bool(self.cams))
        self.zoom.setEnabled(not busy and self.zoom.maximum() > 1.0)
        self.start.setText("Stop" if busy else "Start")
        self.start.setIcon(QIcon.fromTheme("media-playback-stop" if busy else "media-playback-start"))
        self.start.setEnabled(busy or bool(self.cams))

    # ---- devices
    def refresh_devices(self):
        self.set_status("Looking for phones...")
        in_thread(lambda: self.bridge.devices.emit(list_devices()))

    def on_devices(self, devs):
        current = self.device.currentData() or self.settings.value("device", "")
        self.device.blockSignals(True)
        self.device.clear()
        for d in devs:
            self.device.addItem(d["label"], d.get("key", d["serial"]))
            self.device.setItemData(self.device.count() - 1, d, Qt.UserRole + 1)
        self.device.blockSignals(False)
        if not devs:
            self.cams = []
            self.camera.clear()
            self.set_status("No phone found. Plug it in with USB (USB debugging on), "
                            "or enter the IP:port from Wireless debugging and press Connect.")
            self.update_controls()
            return
        idx = self.device.findData(current)
        self.device.setCurrentIndex(max(idx, 0))
        self.on_device_changed()

    def selected_device(self):
        return self.device.currentData(Qt.UserRole + 1)

    def on_device_changed(self, *_):
        d = self.selected_device()
        self.cams = []
        self.camera.clear()
        self.update_controls()
        if not d:
            return
        if d["state"] != "device":
            hint = {"unauthorized": "Unlock the phone and allow USB debugging.",
                    "offline": "Connection lost. Turn Wireless debugging off and on, then Connect again."}
            self.set_status(hint.get(d["state"], f"Phone is {d['state']}."))
            return
        self.set_status("Reading cameras...")
        serial = d["serial"]

        def work():
            try:
                self.bridge.cameras.emit(serial, list_cameras(serial))
            except Exception as e:  # noqa: BLE001
                self.bridge.cameras.emit(serial, e)
        in_thread(work)

    def on_cameras(self, serial, result):
        d = self.selected_device()
        if not d or d["serial"] != serial:
            return
        if isinstance(result, Exception):
            self.set_status(f"Could not read cameras: {result}")
            return
        self.cams = result
        self.camera.blockSignals(True)
        for c in result:
            self.camera.addItem(c["label"], c["id"])
        self.camera.blockSignals(False)
        last = self.settings.value(f"camera/{d['key']}", "")
        idx = self.camera.findData(last)
        self.camera.setCurrentIndex(max(idx, 0))
        self.on_camera_changed()
        self.set_status(f"{len(result)} cameras found. Ready.")
        self.update_controls()

    def on_camera_changed(self, *_):
        cam = next((c for c in self.cams if c["id"] == self.camera.currentData()), None)
        self.zoom.setRange(1.0, cam["zoom"] if cam else 1.0)
        self.zoom.setValue(1.0)
        rates = sorted(cam["fps"]) if cam and cam["fps"] else [30]
        want = int(self.settings.value("fps", 30))
        self.fps.clear()
        for r in rates:
            self.fps.addItem(f"{r} fps", r)
        idx = self.fps.findData(want)
        if idx < 0:  # closest available rate
            idx = min(range(len(rates)), key=lambda i: abs(rates[i] - want))
        self.fps.setCurrentIndex(idx)
        self.update_controls()

    # ---- Wi-Fi
    def wifi_connect(self):
        addr = self.addr.text().strip()
        if not addr:
            self.set_status("Type the IP:port shown under Wireless debugging on the phone.")
            return
        self.settings.setValue("addr", addr)
        self.set_status(f"Connecting to {addr}...")

        def work():
            _, out = run(["adb", "connect", addr], 15)
            self.bridge.message.emit(out.strip())
            self.bridge.devices.emit(list_devices())
        in_thread(work)

    def wifi_pair(self):
        host, ok = QInputDialog.getText(
            self, "Pair phone",
            "On the phone: Wireless debugging > Pair device with pairing code.\n"
            "IP:port shown in that dialog:")
        if not ok or not host.strip():
            return
        code, ok = QInputDialog.getText(self, "Pair phone", "Wi-Fi pairing code:")
        if not ok or not code.strip():
            return
        self.set_status("Pairing...")

        def work():
            _, out = run(["adb", "pair", host.strip(), code.strip()], 30)
            self.bridge.message.emit(out.strip() +
                                     "\nNow enter the IP:port from the main Wireless debugging screen and press Connect.")
        in_thread(work)

    # ---- start / stop
    def toggle(self):
        if self.running():
            self.stop()
        else:
            self.begin()

    def begin(self):
        d = self.selected_device()
        cam = self.camera.currentData()
        if not d or cam is None:
            return
        if not virtual_camera_ready():
            self.set_status("Creating the virtual camera (asks for your password)...")
            QApplication.processEvents()
            code, out = run(["pkexec", "modprobe", "v4l2loopback", f"video_nr={VIDEO_NR}",
                             f"card_label={CAM_LABEL}", "exclusive_caps=1"], 120)
            if code != 0 or not os.path.exists(VIDEO_DEV):
                self.set_status("Could not create the virtual camera. Is v4l2loopback-dkms installed?\n"
                                + out.strip()[-200:])
                return
        if self.mic.isChecked():
            ensure_virtual_mic()

        size = {"480p": 854, "720p": 1280, "1080p": 1920}[self.quality.currentText()]
        rot = self.rotate.currentText().rstrip("°")
        args = ["-s", d["serial"], "--video-source=camera", f"--camera-id={cam}",
                "--camera-ar=16:9", f"--max-size={size}", f"--camera-fps={self.fps.currentData() or 30}",
                f"--v4l2-sink={VIDEO_DEV}", "--no-window"]
        if rot != "0":
            args.append(f"--capture-orientation={rot}")
        if self.zoom.value() > 1.0:
            args.append(f"--camera-zoom={self.zoom.value():g}")
        args += ["--audio-source=mic"] if self.mic.isChecked() else ["--no-audio"]

        for k, v in (("device", d["key"]), (f"camera/{d['key']}", cam),
                     ("quality", self.quality.currentText()),
                     ("fps", self.fps.currentData() or 30),
                     ("rotate", self.rotate.currentText()),
                     ("mic", "true" if self.mic.isChecked() else "false")):
            self.settings.setValue(k, v)

        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self.on_output)
        self.proc.finished.connect(self.on_finished)
        self.log = []
        self.proc.start("scrcpy", args)
        self.set_status("Starting...")
        if self.mic.isChecked():
            self.router.start(1000)
        self.update_controls()

    def on_output(self):
        text = bytes(self.proc.readAllStandardOutput()).decode(errors="replace")
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            self.log.append(line)
            if "v4l2 sink started" in line:
                self.set_status(f"Running. Pick \"{CAM_LABEL}\""
                                + (" and \"PhoneMic\"" if self.mic.isChecked() else "")
                                + " in your app.")
            elif "ERROR" in line:
                self.set_status(line)

    def on_finished(self, code, _status):
        self.router.stop()
        err = [l for l in getattr(self, "log", []) if "ERROR" in l]
        if code not in (0, 2) and err:
            self.set_status("Stopped: " + " ".join(err[-2:]))
        else:
            self.set_status("Stopped.")
        self.proc = None
        self.update_controls()

    def stop(self):
        if self.running():
            self.proc.terminate()
            if not self.proc.waitForFinished(3000):
                self.proc.kill()

    def closeEvent(self, event):
        self.stop()
        super().closeEvent(event)


def main():
    missing = [t for t in ("adb", "scrcpy", "pactl") if not shutil.which(t)]
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setDesktopFileName("phonecam-gui")
    if missing:
        QMessageBox.critical(None, APP_NAME, "Missing programs: " + ", ".join(missing)
                             + "\n\nRun ./install.sh from the Phone Cam folder, or see README.md.")
        return 1
    w = Window()
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
