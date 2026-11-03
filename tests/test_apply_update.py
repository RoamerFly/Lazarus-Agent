from pathlib import Path
import hashlib
import tempfile
import unittest
from unittest.mock import patch

from apply_update import apply_installer_update, apply_portable_update, wait_for_parent


class ApplyUpdateTests(unittest.TestCase):
    def test_portable_update_replaces_only_after_wait_then_restarts(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            source, target = root / "download.exe", root / "Lazarus-Agent.exe"
            source.write_bytes(b"new"); target.write_bytes(b"old")
            with patch("apply_update.wait_for_parent") as waited, patch("apply_update.subprocess.Popen") as launched:
                apply_portable_update(source, target, 1234, hashlib.sha256(b"new").hexdigest())
            waited.assert_called_once_with(1234)
            self.assertEqual(target.read_bytes(), b"new")
            launched.assert_called_once_with([str(target)], close_fds=True)

    def test_installer_relaunches_only_after_success(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            installer, target = root / "setup.exe", root / "Lazarus-Agent.exe"
            installer.write_bytes(b"setup"); target.write_bytes(b"app")
            with patch("apply_update.wait_for_parent"), patch("apply_update.subprocess.run") as run, patch("apply_update.subprocess.Popen") as launched:
                run.return_value.returncode = 0
                run.side_effect = lambda *_args, **_kwargs: ((root / ".lazarus-install-complete").write_text("0.2.1"), run.return_value)[1]
                apply_installer_update(installer, target, 1, hashlib.sha256(b"setup").hexdigest(), "0.2.1")
            run.assert_called_once_with([str(installer), "/S"], check=False)
            launched.assert_called_once_with([str(target)], close_fds=True)

    def test_wait_does_not_replace_a_live_desktop_process(self):
        with patch("apply_update.os.kill", return_value=None), patch("apply_update.time.sleep"):
            with self.assertRaises(RuntimeError):
                wait_for_parent(1, attempts=1)

    def test_tampered_file_after_parent_exit_is_not_started(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            source, target = root / "download.exe", root / "Lazarus-Agent.exe"
            source.write_bytes(b"tampered"); target.write_bytes(b"old")
            with patch("apply_update.wait_for_parent"), patch("apply_update.subprocess.Popen") as launched:
                with self.assertRaises(RuntimeError):
                    apply_portable_update(source, target, 1, hashlib.sha256(b"expected").hexdigest())
            self.assertEqual(target.read_bytes(), b"old")
            launched.assert_not_called()
