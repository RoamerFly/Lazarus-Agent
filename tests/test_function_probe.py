from pathlib import Path
from subprocess import CompletedProcess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from function_probe import probe


PACKAGE = "org.ggbond.example"
TASK = {"tap": "Open route", "expect": "Route ready"}
BEFORE = b'<hierarchy><node package="org.ggbond.example" text="Open route" bounds="[0,0][40,20]"/></hierarchy>'
AFTER = b'<hierarchy><node package="org.ggbond.example" text="Route ready"/></hierarchy>'


def android_anr(title):
    return (f'<hierarchy><node package="android" resource-id="android:id/alertTitle" text="{title}"/>'
            '<node package="android" resource-id="android:id/aerr_wait" text="Wait" '
            'bounds="[0,40][40,60]"/></hierarchy>').encode()


class FunctionProbeTests(unittest.TestCase):
    def exercise(self, frames):
        frames = iter(frames)
        def run(args, timeout=120):
            if "cat" in args:
                xml = next(frames)
                return CompletedProcess(args, 0, xml, b"")
            return CompletedProcess(args, 0, b"", b"")

        with TemporaryDirectory() as tmp, patch("function_probe.invoke", side_effect=run), \
                patch("function_probe.shell", return_value="") as adb, patch("function_probe.time.sleep"):
            evidence = Path(tmp) / "probe"
            result = probe("test", PACKAGE, TASK, evidence)
            captures = {path.name: path.read_bytes() for path in evidence.iterdir()}
        taps = [call.args[3:5] for call in adb.call_args_list if call.args[1:3] == ("input", "tap")]
        dumps = [call.args[1] for call in adb.call_args_list if call.args[1] in ("rm", "uiautomator")]
        self.assertEqual(dumps, ["rm", "uiautomator"] * (len(dumps)//2))
        return result, taps, captures

    def test_waits_for_system_ui_then_requires_fresh_post_action_evidence(self):
        notice = android_anr("System UI isn't responding")
        result, taps, captures = self.exercise([notice, BEFORE, b"invalid frame", BEFORE, AFTER])
        self.assertEqual(result["result"], "passed")
        self.assertTrue(result["system_ui_waited"])
        self.assertEqual(taps, [("20", "50"), ("20", "10")])
        self.assertEqual(captures["before-system-ui-wait.xml"], notice)
        self.assertEqual(captures["after.xml"], AFTER)

    def test_app_anr_is_not_acknowledged_even_with_matching_background_text(self):
        notice = android_anr("Example app isn't responding").replace(b"</hierarchy>", BEFORE[11:])
        result, taps, _ = self.exercise([notice])
        self.assertEqual(result["result"], "insufficient")
        self.assertFalse(result["system_ui_waited"])
        self.assertEqual(taps, [])

    def test_system_ui_wait_is_bounded_and_does_not_replay_the_app_action(self):
        notice = android_anr("System UI isn't responding")
        result, taps, _ = self.exercise([BEFORE, notice, notice])
        self.assertEqual(result["result"], "insufficient")
        self.assertEqual(taps, [("20", "10"), ("20", "50")])
