from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from apply_update import apply_installer_update, apply_portable_update, wait_for_parent


class ApplyUpdateTests(unittest.TestCase):
    def test_portable_update_replaces_only_after_wait_then_restarts(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source, target = root / "download.exe", root / "Lazarus-Agent.exe"
            source.write_bytes(b"new"); target.write_bytes(b"old")
            with patch("apply_update.wait_for_parent") as waited, patch("apply_update.subprocess.Popen") as launched:
                apply_portable_update(source, target, 1234)
            waited.assert_called_once_with(1234)
            self.assertEqual(target.read_bytes(), b"new")
            launched.assert_called_once_with([str(target)], close_fds=True)

    def test_installer_relaunches_only_after_success(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            installer, target = root / "setup.exe", root / "Lazarus-Agent.exe"
            installer.write_bytes(b"setup"); target.write_bytes(b"app")
            with patch("apply_update.wait_for_parent"), patch("apply_update.subprocess.run") as run, patch("apply_update.subprocess.Popen") as launched:
                run.return_value.returncode = 0
                apply_installer_update(installer, target, 1)
            run.assert_called_once_with([str(installer), "/S"], check=False)
            launched.assert_called_once_with([str(target)], close_fds=True)

    def test_wait_does_not_replace_a_live_desktop_process(self):
        with patch("apply_update.os.kill", return_value=None), patch("apply_update.time.sleep"):
            with self.assertRaises(RuntimeError):
                wait_for_parent(1, attempts=1)
