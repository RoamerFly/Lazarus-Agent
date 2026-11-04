import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
import desktop

class DesktopWindowTests(unittest.TestCase):
    def test_native_window_profile_and_icon(self):
        host=SimpleNamespace(settings={},create_window=Mock(),start=Mock())
        with patch.dict('sys.modules',{'webview':host,'webview.platforms.winforms':SimpleNamespace(is_chromium=True)}),patch.object(desktop.ctypes,'windll',SimpleNamespace(shell32=Mock()),create=True):
            desktop.show_window('http://127.0.0.1:8767')
        self.assertTrue(host.settings['ALLOW_DOWNLOADS'])
        self.assertTrue(host.create_window.call_args.kwargs['maximized'])
        self.assertEqual(host.start.call_args.kwargs['gui'],'edgechromium')
        self.assertTrue(Path(host.start.call_args.kwargs['icon']).is_file())
        self.assertTrue(host.start.call_args.kwargs['storage_path'].endswith('desktop-webview2'))
        self.assertEqual(host.create_window.call_args.args[1],'http://127.0.0.1:8767')

    def test_missing_runtime_has_an_actionable_error(self):
        host=SimpleNamespace(create_window=Mock())
        with patch.dict('sys.modules',{'webview':host,'webview.platforms.winforms':SimpleNamespace(is_chromium=False)}):
            with self.assertRaisesRegex(RuntimeError,'WebView2 Runtime'):
                desktop.show_window('http://127.0.0.1:8767')
        host.create_window.assert_not_called()
