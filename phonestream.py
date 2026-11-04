"""Direct phone camera/mic stream, for platforms without scrcpy's v4l2 sink.

On Linux, Phone Cam lets the scrcpy client write into a v4l2loopback device.
Windows has no such sink. Instead, this module starts the scrcpy *server*
on the phone through adb, reads its raw H.264 video and raw PCM audio
over the adb tunnel, decodes the video with PyAV, and sends the frames to a
virtual camera (pyvirtualcam: "OBS Virtual Camera" on Windows) and the
audio to an output device (sounddevice: "CABLE Input" from VB-CABLE).

The scrcpy server binary must be the same version as the scrcpy release
it came with. Both ship together in the Windows package.
"""

import random
import socket
import subprocess
import threading
import time

SAMPLE_RATE = 48000
CHANNELS = 2
DEVICE_PATH = "/data/local/tmp/phonecam-server.jar"


def _no_window():
    """Hide console windows for child processes on Windows."""
    import sys
    if sys.platform == "win32":
        return {"creationflags": 0x08000000}  # CREATE_NO_WINDOW
    return {}


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class PhoneStream:
    """Stream one phone camera (and optionally its mic).

    Callbacks run on worker threads:
      on_status(text), on_started(width, height), on_stopped(error_or_None)
    """

    def __init__(self, adb, server_path, server_version, serial, camera_id,
                 size=None, fps=30, rotate=0, zoom=1.0, mic=False,
                 audio_device=None, on_status=None, on_started=None, on_stopped=None):
        self.adb = adb
        self.server_path = server_path
        self.version = server_version
        self.serial = serial
        self.camera_id = camera_id
        self.size = size
        self.fps = fps
        self.rotate = rotate
        self.zoom = zoom
        self.mic = mic
        self.audio_device = audio_device
        self.on_status = on_status or (lambda t: None)
        self.on_started = on_started or (lambda w, h: None)
        self.on_stopped = on_stopped or (lambda e: None)
        self._stop = threading.Event()
        self._server = None
        self._socks = []
        self._port = None
        self._log = []
        self._thread = None

    # ------------------------------------------------------------ control
    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        for s in self._socks:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            s.close()
        if self._server and self._server.poll() is None:
            self._server.terminate()
            try:
                self._server.wait(3)
            except subprocess.TimeoutExpired:
                self._server.kill()
        if self._thread and threading.current_thread() is not self._thread:
            self._thread.join(5)

    def running(self):
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------ helpers
    def _adb(self, *args, timeout=20):
        return subprocess.run([self.adb, "-s", self.serial, *args], capture_output=True,
                              text=True, timeout=timeout, **_no_window())

    def _server_args(self, scid):
        a = [f"scid={scid:08x}", "log_level=info", "tunnel_forward=true",
             "video=true", "video_codec=h264", "video_source=camera",
             f"camera_id={self.camera_id}", f"camera_fps={self.fps}",
             "control=false", "cleanup=true",
             "send_device_meta=false", "send_frame_meta=false",
             "send_stream_meta=false", "send_dummy_byte=true"]
        if self.size:
            a.append(f"camera_size={self.size[0]}x{self.size[1]}")
        else:
            a += ["camera_ar=16:9", "max_size=1280"]
        if self.rotate:
            a.append(f"capture_orientation={self.rotate}")
        if self.zoom and self.zoom > 1.0:
            a.append(f"camera_zoom={self.zoom:g}")
        if self.mic:
            a += ["audio=true", "audio_codec=raw", "audio_source=mic"]
        else:
            a.append("audio=false")
        return a

    def _connect(self, deadline):
        """Connect to the server through the adb forward (retries until it listens)."""
        while time.time() < deadline and not self._stop.is_set():
            if self._server.poll() is not None:
                raise RuntimeError(self._server_error() or "The phone stopped the camera server")
            s = socket.create_connection(("127.0.0.1", self._port), timeout=5)
            try:
                s.settimeout(2)
                if s.recv(1):          # dummy byte: the server is ready
                    s.settimeout(None)
                    return s
            except (socket.timeout, ConnectionError, OSError):
                pass
            s.close()
            time.sleep(0.15)
        raise RuntimeError(self._server_error() or "Timed out waiting for the phone")

    def _second_socket(self):
        s = socket.create_connection(("127.0.0.1", self._port), timeout=5)
        s.settimeout(None)
        return s

    def _server_error(self):
        for line in self._log:
            if "Exception:" in line or "Error:" in line:
                return line.split(":", 1)[1].strip() if ":" in line else line
        errs = [l for l in self._log if "ERROR" in l]
        return errs[0] if errs else ""

    def _read_server_log(self):
        for line in self._server.stdout:
            line = line.strip()
            if line:
                self._log.append(line)

    # ------------------------------------------------------------ main
    def _run(self):
        error = None
        try:
            self._start_server()
            deadline = time.time() + 15
            video = self._connect(deadline)
            self._socks.append(video)
            audio = None
            if self.mic:
                audio = self._second_socket()
                self._socks.append(audio)
                threading.Thread(target=self._audio_loop, args=(audio,), daemon=True).start()
            self._video_loop(video)
        except Exception as e:  # noqa: BLE001
            if not self._stop.is_set():
                error = str(e) or e.__class__.__name__
        finally:
            if not self._stop.is_set() and not error:
                error = self._server_error() or None
            self._cleanup()
            self.on_stopped(error)

    def _start_server(self):
        self.on_status("Sending camera server to the phone...")
        r = self._adb("push", self.server_path, DEVICE_PATH, timeout=60)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout).strip() or "adb push failed")
        scid = random.getrandbits(31)
        self._port = _free_port()
        r = self._adb("forward", f"tcp:{self._port}", f"localabstract:scrcpy_{scid:08x}")
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout).strip() or "adb forward failed")
        cmd = [self.adb, "-s", self.serial, "shell",
               f"CLASSPATH={DEVICE_PATH}", "app_process", "/",
               "com.genymobile.scrcpy.Server", self.version, *self._server_args(scid)]
        self._server = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        stdin=subprocess.DEVNULL, text=True, errors="replace",
                                        **_no_window())
        threading.Thread(target=self._read_server_log, daemon=True).start()
        self.on_status("Starting the camera...")

    def _cleanup(self):
        for s in self._socks:
            try:
                s.close()
            except OSError:
                pass
        if self._server and self._server.poll() is None:
            self._server.terminate()
            try:
                self._server.wait(3)
            except subprocess.TimeoutExpired:
                self._server.kill()
        if self._port:
            try:
                self._adb("forward", "--remove", f"tcp:{self._port}", timeout=5)
            except Exception:  # noqa: BLE001
                pass

    def _video_loop(self, sock):
        import av
        import numpy as np
        import pyvirtualcam

        codec = av.CodecContext.create("h264", "r")
        cam = None
        size = None
        try:
            while not self._stop.is_set():
                data = sock.recv(1 << 16)
                if not data:
                    break
                for packet in codec.parse(data):
                    for frame in codec.decode(packet):
                        if cam is None or (frame.width, frame.height) != size:
                            if cam:
                                cam.close()
                            size = (frame.width, frame.height)
                            cam = pyvirtualcam.Camera(width=size[0], height=size[1],
                                                      fps=self.fps, fmt=pyvirtualcam.PixelFormat.RGB,
                                                      print_fps=False)
                            self.on_started(*size)
                        img = frame.to_ndarray(format="rgb24")
                        cam.send(np.ascontiguousarray(img))
        finally:
            if cam:
                cam.close()

    def _audio_loop(self, sock):
        try:
            import sounddevice as sd
        except Exception as e:  # noqa: BLE001
            self.on_status(f"Microphone unavailable: {e}")
            return
        try:
            out = sd.RawOutputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="int16",
                                     device=self.audio_device, latency="low")
        except Exception as e:  # noqa: BLE001
            self.on_status(f"Microphone unavailable: {e}")
            return
        with out:
            buf = b""
            frame_bytes = CHANNELS * 2
            while not self._stop.is_set():
                data = sock.recv(1 << 14)
                if not data:
                    break
                buf += data
                n = len(buf) - len(buf) % frame_bytes
                if n:
                    out.write(buf[:n])
                    buf = buf[n:]


def find_audio_device(name_part="CABLE Input"):
    """Index of the output device whose name contains name_part (VB-CABLE), or None."""
    try:
        import sounddevice as sd
        for i, d in enumerate(sd.query_devices()):
            if name_part.lower() in d["name"].lower() and d["max_output_channels"] >= 2:
                return i
    except Exception:  # noqa: BLE001
        pass
    return None
