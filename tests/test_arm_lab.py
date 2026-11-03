import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import bench_device as bench
from game_probe import system_prompt
from tools.pacific_game_checkpoints import ROOT, gray


class ArmLabTests(unittest.TestCase):
    def test_requires_pairing_and_runtime_identity(self):
        token = "a" * 64
        valid = [token, "36", "redroid_arm64", "armeabi-v7a,armeabi", "1"]
        with tempfile.TemporaryDirectory() as tmp:
            pairing = Path(tmp) / "instance"
            pairing.write_text(token)
            with patch.object(bench, "ARM_PAIRING", pairing):
                for lines, expected in [
                    (valid, True), (["b"*64, *valid[1:]], False),
                    ([token, "35", *valid[2:]], False), ([*valid[:3], "", "1"], False),
                    ([*valid[:4], "0"], False),
                ]:
                    response = CompletedProcess([], 0, "\n".join(lines).encode(), b"")
                    with patch.object(bench, "invoke", return_value=response):
                        self.assertEqual(bench.paired_arm_lab(), expected)

    def test_arm32_routing(self):
        device = bench.Device()
        with patch.object(bench, "ensure_arm_lab", return_value=True) as arm:
            self.assertEqual(device.ensure("job", 36, lambda _: None, ["armeabi-v7a", "x86"]), bench.ARM_LAB)
            arm.assert_called_once()
        with patch.object(bench, "paired_arm_lab", return_value=False):
            with self.assertRaises(ValueError):
                device.check("job")

    def test_compatibility_notice_with_shifted_window(self):
        # Move the reference dialog by the vertical offset observed when a
        # second system window was underneath it. App-owned copies must fail.
        frame = gray(ROOT / "redroid16-canary-screen.png")
        moved = frame.copy()
        moved[:-11] = frame[11:]
        self.assertEqual(system_prompt("u0 DeprecatedAbiDialog}", moved), (520, 274))
        self.assertIsNone(system_prompt("u0 com.jumpgames.pacificrim/.Welcome", moved))
        self.assertIsNone(system_prompt("u0 DeprecatedAbiDialog}", moved * 0))


if __name__ == "__main__":
    unittest.main()
