import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import modification_probe


FACTS = {"package": "org.example.reader", "launcher": ".ReaderActivity"}
CRITERIA = {"selects": [{"tap": "Open B", "expect": "Record B"},
                        {"tap": "Open A", "expect": "Record A"}]}


class ModificationProbeTests(unittest.TestCase):
    def test_rejects_unbounded_or_invalid_steps(self):
        with self.assertRaisesRegex(ValueError, "1 至"):
            modification_probe._validate(FACTS, {"selects": []})
        with self.assertRaisesRegex(ValueError, "包名"):
            modification_probe._validate({"package": "bad;", "launcher": ".A"}, CRITERIA)

    def test_missing_app_control_is_insufficient_and_preserves_snapshot(self):
        xml = b'<hierarchy><node package="org.example.reader" text="Other" bounds="[0,0][1,1]"/></hierarchy>'
        class Result:
            returncode = 0
            stdout = xml
            stderr = b""
        with tempfile.TemporaryDirectory() as temp, patch("modification_probe.shell", return_value=""), \
             patch("modification_probe.invoke", return_value=Result()):
            report = modification_probe.verify_behavior("emulator-1", FACTS,
                                                        {"selects": [{"tap": "Open B", "expect": "Record B"}]},
                                                        Path(temp) / "evidence")
            self.assertEqual(report["result"], "insufficient")
            self.assertTrue((Path(temp) / "evidence" / "step-01-before-select.xml").is_file())

    def test_cold_start_failure_is_distinct_from_missing_control(self):
        trees = iter([
            b'<hierarchy><node package="org.example.reader" text="Open B" bounds="[0,0][4,4]"/></hierarchy>',
            b'<hierarchy><node package="org.example.reader" text="Record B" bounds="[0,0][4,4]"/></hierarchy>',
            b'<hierarchy><node package="org.example.reader" text="Choose a record" bounds="[0,0][4,4]"/></hierarchy>',
        ])
        class Result:
            returncode = 0
            stderr = b""
            def __init__(self, stdout=b""): self.stdout = stdout
        def invoke(args, *unused):
            if "exec-out" in args and "cat" in args:
                return Result(next(trees))
            if "exec-out" in args:
                return Result(b"")
            if "am" in args:
                return Result(b"Status: ok")
            if "dumpsys" in args:
                return Result(b"mCurrentFocus=Window{123 u0 org.example.reader/.ReaderActivity}")
            return Result()
        with tempfile.TemporaryDirectory() as temp, patch("modification_probe.shell", return_value=""), \
             patch("modification_probe.invoke", side_effect=invoke), patch("modification_probe.time.sleep"):
            report = modification_probe.verify_behavior("emulator-1", FACTS,
                                                        {"selects": [{"tap": "Open B", "expect": "Record B"}]},
                                                        Path(temp) / "evidence")
            self.assertEqual(report["result"], "failed")
            self.assertFalse(report["steps"][0]["cold_start"])

    def test_non_foreground_cold_start_is_insufficient(self):
        trees = iter([
            b'<hierarchy><node package="org.example.reader" text="Open B" bounds="[0,0][4,4]"/></hierarchy>',
            b'<hierarchy><node package="org.example.reader" text="Record B" bounds="[0,0][4,4]"/></hierarchy>',
            b'<hierarchy><node package="android" text="Home" bounds="[0,0][4,4]"/></hierarchy>',
        ])
        class Result:
            returncode = 0
            stderr = b""
            def __init__(self, stdout=b""): self.stdout = stdout
        def invoke(args, *unused):
            if "exec-out" in args and "cat" in args: return Result(next(trees))
            if "exec-out" in args: return Result()
            if "dumpsys" in args: return Result(b"mCurrentFocus=Window{1 u0 com.android.launcher/.Home}")
            return Result(b"Status: ok")
        with tempfile.TemporaryDirectory() as temp, patch("modification_probe.shell", return_value=""), \
             patch("modification_probe.invoke", side_effect=invoke), patch("modification_probe.time.sleep"):
            report = modification_probe.verify_behavior("emulator-1", FACTS,
                                                        {"selects": [{"tap": "Open B", "expect": "Record B"}]},
                                                        Path(temp) / "evidence")
        self.assertEqual(report["result"], "insufficient")

    def test_next_selection_must_remove_previous_record(self):
        trees = iter([
            b'<hierarchy><node package="org.example.reader" text="Open B" bounds="[0,0][4,4]"/></hierarchy>',
            b'<hierarchy><node package="org.example.reader" text="Record B" bounds="[0,0][4,4]"/></hierarchy>',
            b'<hierarchy><node package="org.example.reader" text="Record B" bounds="[0,0][4,4]"/></hierarchy>',
            b'<hierarchy><node package="org.example.reader" text="Open A" bounds="[0,0][4,4]"/></hierarchy>',
            b'<hierarchy><node package="org.example.reader" text="Record A" bounds="[0,0][4,4]"/><node package="org.example.reader" text="Record B" bounds="[0,4][4,8]"/></hierarchy>',
        ])
        class Result:
            returncode = 0
            stderr = b""
            def __init__(self, stdout=b""): self.stdout = stdout
        def invoke(args, *unused):
            if "exec-out" in args and "cat" in args: return Result(next(trees))
            if "exec-out" in args: return Result()
            if "dumpsys" in args: return Result(b"mCurrentFocus=Window{1 u0 org.example.reader/.ReaderActivity}")
            return Result(b"Status: ok")
        with tempfile.TemporaryDirectory() as temp, patch("modification_probe.shell", return_value=""), \
             patch("modification_probe.invoke", side_effect=invoke), patch("modification_probe.time.sleep"):
            report = modification_probe.verify_behavior("emulator-1", FACTS, CRITERIA,
                                                        Path(temp) / "evidence")
        self.assertEqual(report["result"], "failed")
        self.assertEqual(report["failed_step"], 2)

    def test_unchanged_screen_after_tap_is_insufficient(self):
        xml = b'<hierarchy><node package="org.example.reader" text="Open B" bounds="[0,0][4,4]"/></hierarchy>'
        trees = iter([xml, xml])
        class Result:
            returncode = 0
            stderr = b""
            def __init__(self, stdout=b""): self.stdout = stdout
        def invoke(args, *unused):
            if "exec-out" in args and "cat" in args: return Result(next(trees))
            return Result()
        with tempfile.TemporaryDirectory() as temp, patch("modification_probe.shell", return_value=""), \
             patch("modification_probe.invoke", side_effect=invoke), patch("modification_probe.time.sleep"):
            report = modification_probe.verify_behavior("emulator-1", FACTS,
                                                        {"selects": [{"tap": "Open B", "expect": "Record B"}]},
                                                        Path(temp) / "evidence")
        self.assertEqual(report["result"], "insufficient")
