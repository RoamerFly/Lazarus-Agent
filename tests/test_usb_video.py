"""Exercise live-stream decoding without an attached phone or saved phone image."""
from fractions import Fraction
import threading
import time
import unittest
from unittest.mock import patch

import av
import numpy as np

from usb_video import UsbVideo


class _Output:
    def __init__(self, data, stopped):
        self.data = data if isinstance(data, list) else [(0, data)]
        self.stopped = stopped

    def read1(self, unused):
        if self.data:
            delay, data = self.data.pop(0)
            if delay:
                self.stopped.wait(delay)
            return data
        self.stopped.wait(5)
        return b""

    def close(self):
        self.stopped.set()


class _Process:
    def __init__(self, data):
        self.stopped = threading.Event()
        self.stdout = _Output(data, self.stopped)

    def poll(self):
        return 0 if self.stopped.is_set() else None

    def terminate(self):
        self.stopped.set()

    def kill(self):
        self.stopped.set()

    def wait(self, timeout=None):
        self.stopped.wait(timeout)
        return 0


def one_h264_frame():
    encoder = av.CodecContext.create("libx264", "w")
    encoder.width, encoder.height = 64, 96
    encoder.pix_fmt = "yuv420p"
    encoder.time_base = Fraction(1, 30)
    encoder.options = {"tune": "zerolatency", "preset": "ultrafast"}
    frame = av.VideoFrame.from_ndarray(np.zeros((96, 64, 3), dtype=np.uint8), format="rgb24")
    frame.pts = 0
    return b"".join(map(bytes, encoder.encode(frame) + encoder.encode(None)))


def two_h264_frames():
    encoder = av.CodecContext.create("libx264", "w")
    encoder.width, encoder.height = 64, 96
    encoder.pix_fmt = "yuv420p"
    encoder.time_base = Fraction(1, 30)
    encoder.options = {"tune": "zerolatency", "preset": "ultrafast"}
    packets = []
    for pts, brightness in enumerate((0, 255)):
        frame = av.VideoFrame.from_ndarray(np.full((96, 64, 3), brightness, dtype=np.uint8), format="rgb24")
        frame.pts = pts
        packets.extend(encoder.encode(frame))
    packets.extend(encoder.encode(None))
    return [(0, bytes(packets[0])), (.4, bytes(packets[1]))]


class UsbVideoTests(unittest.TestCase):
    def test_still_screen_yields_frame_and_does_not_restart_stream(self):
        process = _Process(one_h264_frame())
        with patch("usb_video.subprocess.Popen", return_value=process) as popen:
            video = UsbVideo("phone", 64, 96)
            try:
                deadline = time.monotonic() + 2
                while video.frames == 0 and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertEqual(video.frames, 1, video.error)
                self.assertTrue(video.latest()["data"].startswith(b"\xff\xd8"))
                time.sleep(.3)
                self.assertTrue(video._thread.is_alive())
                popen.assert_called_once()
            finally:
                video.stop()
                video._thread.join(2)
        self.assertFalse(video._thread.is_alive())

    def test_sleeping_display_error_exits_without_decoder_wait(self):
        process = _Process(b"ERROR: INVALID_LAYER_STACK, please check your display state.\n")
        with patch("usb_video.subprocess.Popen", return_value=process):
            video = UsbVideo("phone", 64, 96)
            video._thread.join(2)
        self.assertFalse(video._thread.is_alive())
        self.assertEqual(video.frames, 0)
        self.assertIn("INVALID_LAYER_STACK", video.error)

    def test_later_screen_change_uses_the_same_stream(self):
        process = _Process(two_h264_frames())
        with patch("usb_video.subprocess.Popen", return_value=process) as popen:
            video = UsbVideo("phone", 64, 96)
            try:
                deadline = time.monotonic() + 2
                while video.frames < 2 and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertEqual(video.frames, 2, video.error)
                self.assertTrue(video._thread.is_alive())
                popen.assert_called_once()
            finally:
                video.stop()
                video._thread.join(2)


if __name__ == "__main__":
    unittest.main()
