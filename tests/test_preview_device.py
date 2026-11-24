from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
import unittest
import struct
import time
from unittest.mock import Mock, patch

import cv2
import numpy as np
from preview_device import Preview, UsbDevice, usb_devices, runtime_evidence


def completed(stdout=b"", code=0):
    return SimpleNamespace(stdout=stdout, stderr=b"", returncode=code)


class PreviewTests(unittest.TestCase):
    def test_usb_video_frame_and_rotation_guard_preserve_coordinate_scope(self):
        preview = Preview(Mock())
        preview.selected = "phone"
        video = Mock()
        video.width, video.height = 1080, 2400
        video.latest.return_value = {"data": b"jpeg", "width": 1080, "height": 2400,
                                     "capture_mode": "h264-stream", "capture_ms": 0}
        preview._video = video
        preview._geometry_checked = time.monotonic()
        self.assertEqual(preview.frame("job")["capture_mode"], "h264-stream")
        with patch.object(preview, "_usb_geometry", return_value=(2400, 1080)), patch("preview_device.bench.shell") as shell:
            with self.assertRaisesRegex(ValueError, "方向"):
                preview.interact("job", {"event": "tap", "x": 100, "y": 100})
            shell.assert_not_called()
        video.stop.assert_called_once()
        self.assertIsNone(preview._frame)
        self.assertIsNone(preview._video)

    def test_unsupported_raw_layout_falls_back_to_png(self):
        png = cv2.imencode(".png", np.zeros((120, 80, 3), dtype=np.uint8))[1].tobytes()
        preview = Preview(Mock(serial="emulator-5578"))
        with patch("preview_device.bench.invoke", side_effect=[completed(b"unsupported"), completed(png)]):
            frame = preview.frame("job")
        self.assertEqual(frame["capture_mode"], "png-fallback")
        self.assertEqual((frame["width"], frame["height"]), (80, 120))

    def test_runtime_logs_are_pid_scoped_without_clearing(self):
        responses = [completed(b"123"), completed(b"Process: other.app, PID: 999"), completed(b"owned log")]
        with patch("preview_device.bench.invoke", side_effect=responses) as invoke:
            evidence = runtime_evidence("phone", "sample.app")
        self.assertEqual(evidence["log"], "owned log")
        commands = [list(map(str, call.args[0])) for call in invoke.call_args_list]
        self.assertIn("--pid", commands[-1]); self.assertIn("123", commands[-1])
        self.assertFalse(any("-c" in command for command in commands))

    def test_usb_inventory_excludes_emulators_and_marks_authorization(self):
        listing = b"List of devices attached\nusb123 device usb:1 model:Test_Phone\nusb456 unauthorized usb:2\nemulator-5578 device\n127.0.0.1:19636 device\n"
        with patch("preview_device.bench.invoke", return_value=completed(listing)):
            devices = usb_devices()
        self.assertEqual([d["id"] for d in devices], ["usb123", "usb456"])
        self.assertFalse(devices[1]["available"])

    def test_frame_cache_jpeg_original_dimensions_and_stale_input(self):
        pixels = np.random.default_rng(0).integers(0,256,(2400,1080,4),dtype=np.uint8)
        raw = struct.pack("<4I", 1080, 2400, 1, 1) + pixels.tobytes()
        device = Mock(serial="emulator-5578")
        preview = Preview(device)
        with patch("preview_device.bench.invoke", return_value=completed(raw)) as capture:
            frame = preview.frame("job")
            self.assertEqual(preview.frame("job"), frame)
            self.assertEqual(capture.call_count, 1)
        self.assertEqual((frame["width"], frame["height"]), (1080, 2400))
        self.assertLess(len(frame["data"]), len(raw))
        decoded = cv2.imdecode(np.frombuffer(frame["data"], dtype=np.uint8), cv2.IMREAD_COLOR)
        self.assertEqual(decoded.shape[0], 1280)
        with patch("preview_device.bench.shell") as shell:
            with self.assertRaises(ValueError):
                preview.interact("job", {"event": "tap", "x": 1, "y": 1, "generation": 99})
            with self.assertRaises(ValueError):
                preview.interact("job", {"event": "tap", "x": 1080, "y": 1})
            preview.interact("job", {"event": "swipe", "x": 540, "y": 2000, "x2": 540, "y2": 300})
            self.assertEqual(shell.call_args.args[1:4], ("input", "swipe", "540"))

    def test_switch_does_not_retain_old_device_frame(self):
        preview = Preview(Mock())
        preview._frame = ("old", 0, {})
        with patch("preview_device.usb_devices", return_value=[{"id": "phone", "available": True}]):
            preview.select("phone")
        self.assertIsNone(preview._frame)
        self.assertEqual(preview.generation, 1)

    def test_phone_signature_failure_never_uninstalls_or_clears_data(self):
        device = UsbDevice("phone")
        device.owner, device.api = "job", 36
        calls = []
        def invoke(args, *unused):
            calls.append(list(map(str, args)))
            return completed(b"Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE]", 1)
        with patch("preview_device.bench.tool_input", return_value=nullcontext(Path("sample.apk"))), \
                patch("preview_device.bench.invoke", side_effect=invoke), patch("preview_device.bench.shell", return_value="arm64-v8a"):
            result = device.install_run(Path("sample.apk"), {"package": "sample.app", "launcher": ".Main"})
        self.assertEqual(result["install"]["exit"], 1)
        self.assertIn("-r", calls[0])
        self.assertNotIn("uninstall", str(calls)); self.assertNotIn("clear", str(calls))
