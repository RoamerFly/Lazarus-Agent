"""USB screen preview using Android's built-in H.264 output and bundled OpenCV.

No APK, audio, recording file or external network service is involved.
"""
import os
import socket
import subprocess
import threading
import time

import bench_device as bench

_OPEN_LOCK = threading.Lock()


class UsbVideo:
    def __init__(self, serial, width, height):
        self.serial, self.width, self.height = serial, width, height
        self._stop = threading.Event()
        self._frame = None
        self._process = self._connection = None
        self._touched = time.monotonic()
        self.error = None
        self.frames = 0
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        threading.Thread(target=self._watchdog, daemon=True).start()

    def latest(self):
        self._touched = time.monotonic()
        return self._frame if self._thread.is_alive() and not self._stop.is_set() else None

    def stop(self):
        self._stop.set()
        if self._connection:
            try:
                self._connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if self._process and self._process.poll() is None:
            self._process.terminate()

    def _watchdog(self):
        while not self._stop.wait(1):
            if time.monotonic() - self._touched > 5 or not self._thread.is_alive():
                self.stop()
                return

    def _run(self):
        import cv2
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(6)
        port = listener.getsockname()[1]
        capture = None
        scale = min(1, 1280 / max(self.width, self.height))
        width = max(16, round(self.width * scale / 16) * 16)
        height = max(16, round(self.height * scale / 16) * 16)

        def forward():
            try:
                connection, _ = listener.accept()
                self._connection = connection
                with connection:
                    if self._stop.is_set():
                        return
                    self._process = subprocess.Popen(
                        [str(bench.ADB), "-s", self.serial, "exec-out", "screenrecord", "--output-format=h264",
                         "--size", f"{width}x{height}", "--bit-rate", "1500000", "--time-limit", "180", "-"],
                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    while not self._stop.is_set():
                        data = self._process.stdout.read1(65536)
                        if not data:
                            break
                        connection.sendall(data)
            except (OSError, ValueError):
                pass
            finally:
                self.stop()

        threading.Thread(target=forward, daemon=True).start()
        try:
            # OpenCV's bundled FFmpeg decodes only our loopback H.264 connection.
            with _OPEN_LOCK:
                old = os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS")
                os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "fflags;nobuffer|flags;low_delay|probesize;32768|analyzeduration;0"
                try:
                    capture = cv2.VideoCapture(f"tcp://127.0.0.1:{port}", cv2.CAP_FFMPEG,
                                              [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000,
                                               cv2.CAP_PROP_READ_TIMEOUT_MSEC, 30000])
                finally:
                    if old is None:
                        os.environ.pop("OPENCV_FFMPEG_CAPTURE_OPTIONS", None)
                    else:
                        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = old
            if not capture.isOpened():
                raise RuntimeError("设备视频预览不可用，使用截图预览")
            encoded_at = 0
            while not self._stop.is_set():
                ok, image = capture.read()
                if not ok:
                    break
                now = time.monotonic()
                # Drain the decoder continuously; retain only the newest frame.
                if now - encoded_at < .075:
                    continue
                encoded_at = now
                ok, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 70])
                if ok:
                    self.frames += 1
                    self._frame = {"data": jpeg.tobytes(), "width": self.width, "height": self.height,
                                   "capture_ms": 0, "capture_mode": "h264-stream", "produced_at": now}
        except Exception as exc:
            self.error = str(exc)[:200]
        finally:
            self.stop()
            if capture is not None:
                capture.release()
            listener.close()
            if self._process:
                try:
                    self._process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                if self._process.stdout:
                    self._process.stdout.close()
