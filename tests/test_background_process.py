import sys
import unittest
from bench_device import invoke
from reviver import run


@unittest.skipUnless(sys.platform=="win32", "Windows console behavior")
class BackgroundProcessTests(unittest.TestCase):
    def test_android_and_signing_commands_have_no_console(self):
        args=[sys.executable,"-c","import ctypes; print(ctypes.windll.kernel32.GetConsoleWindow())"]
        self.assertEqual(invoke(args).stdout.strip(),b"0")
        self.assertEqual(run(args).strip(),"0")
