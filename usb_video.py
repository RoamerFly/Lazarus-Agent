"""Decode a USB phone's H.264 preview without installing anything on the phone."""
import queue
import subprocess
import threading
import time

import bench_device as bench


_AUD = b"\x00\x00\x00\x01\x09\xf0"


class UsbVideo:
    def __init__(self, serial, width, height):
        self.serial, self.width, self.height = serial, width, height
        self._stop = threading.Event()
        self._frame = None
        self._process = None
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
        if self._process and self._process.poll() is None:
            self._process.terminate()

    def _watchdog(self):
        while not self._stop.wait(1):
            if time.monotonic() - self._touched > 5 or not self._thread.is_alive():
                self.stop()
                return

    def _run(self):
        import av
        import cv2

        scale = min(1, 1280 / max(self.width, self.height))
        width = max(16, round(self.width * scale / 16) * 16)
        height = max(16, round(self.height * scale / 16) * 16)
        chunks = queue.Queue(maxsize=64)

        def read_stream():
            try:
                while not self._stop.is_set():
                    data = self._process.stdout.read1(65536)
                    chunks.put(data, timeout=1)
                    if not data:
                        return
            except (OSError, ValueError, queue.Full):
                self.error = "设备视频传输中断"
                self.stop()

        try:
            self._process = subprocess.Popen(
                [str(bench.ADB), "-s", self.serial, "exec-out", "screenrecord", "--output-format=h264",
                 "--size", f"{width}x{height}", "--bit-rate", "1500000", "--time-limit", "180", "-"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            threading.Thread(target=read_stream, daemon=True).start()
            decoder = av.CodecContext.create("h264", "r")
            encoded_at = 0
            pending = False
            while not self._stop.is_set():
                try:
                    data = chunks.get(timeout=.12)
                    if data.startswith(b"ERROR:"):
                        # A sleeping display can emit INVALID_LAYER_STACK on
                        # stdout and still let screenrecord exit successfully.
                        self.error = data.decode("utf-8", "replace").strip()[:200]
                        break
                    if data:
                        pending = True
                except queue.Empty:
                    if not pending:
                        continue
                    # A still screen may emit only one access unit. Release it
                    # without ending the decoder so later changes still arrive.
                    data = _AUD
                    pending = False
                for packet in decoder.parse(data):
                    for image in decoder.decode(packet):
                        now = time.monotonic()
                        if now - encoded_at < .075:
                            continue
                        ok, jpeg = cv2.imencode(".jpg", image.to_ndarray(format="bgr24"),
                                                [cv2.IMWRITE_JPEG_QUALITY, 70])
                        if ok:
                            encoded_at = now
                            self.frames += 1
                            self._frame = {"data": jpeg.tobytes(), "width": self.width, "height": self.height,
                                           "capture_ms": 0, "capture_mode": "h264-stream", "produced_at": now}
                if not data:
                    break
        except Exception as exc:
            self.error = str(exc)[:200]
        finally:
            self.stop()
            if self._process:
                try:
                    self._process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                if self._process.stdout:
                    self._process.stdout.close()
