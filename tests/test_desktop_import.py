import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from desktop_import import DesktopImport, apk_directory


class DesktopImportTests(unittest.TestCase):
    def test_picker_uses_directory_next_to_frozen_exe(self):
        with TemporaryDirectory() as directory:
            exe = Path(directory) / "Lazarus-Agent.exe"
            with patch("sys.frozen", True, create=True), patch("sys.executable", str(exe)):
                self.assertEqual(apk_directory(), Path(directory) / "old_apk")
                self.assertTrue(apk_directory().is_dir())

    def test_selected_file_uses_existing_authenticated_import_route(self):
        with TemporaryDirectory() as directory:
            apk = Path(directory) / "sample.apk"
            apk.write_bytes(b"selected-apk")
            bridge = DesktopImport("http://127.0.0.1:8767")
            bridge._window = Mock()
            bridge._window.create_file_dialog.return_value = [str(apk)]
            requests = []
            def respond(request, **kwargs):
                requests.append(request)
                if isinstance(request, str):
                    return io.BytesIO(json.dumps({"token": "local-token"}).encode())
                self.assertEqual(request.data.read(), b"selected-apk")
                return io.BytesIO(b'{"id":"imported"}')
            with patch.dict("sys.modules", {"webview": SimpleNamespace(FileDialog=SimpleNamespace(OPEN=10))}), patch("desktop_import.apk_directory", return_value=Path(directory)), patch("desktop_import.urlopen", side_effect=respond):
                self.assertEqual(bridge.choose_apk(36), {"job": {"id": "imported"}})
            self.assertEqual(bridge._window.create_file_dialog.call_args.kwargs["directory"], directory)
            self.assertEqual(requests[1].full_url, "http://127.0.0.1:8767/api/import/36")
            self.assertEqual(requests[1].get_header("X-reviver-token"), "local-token")

    def test_cancel_or_invalid_file_does_not_send_file(self):
        bridge = DesktopImport("http://127.0.0.1:8767")
        bridge._window = Mock()
        bridge._window.create_file_dialog.return_value = None
        with patch.dict("sys.modules", {"webview": SimpleNamespace(FileDialog=SimpleNamespace(OPEN=10))}), patch("desktop_import.urlopen") as send, patch("desktop_import.apk_directory", return_value=Path(".")):
            self.assertEqual(bridge.choose_apk(36), {"cancelled": True})
            self.assertIn("error", bridge.choose_apk("36"))
            send.assert_not_called()
