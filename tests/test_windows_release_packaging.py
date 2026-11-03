from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class WindowsReleasePackagingTests(unittest.TestCase):
    def test_nsis_is_per_user_silent_and_never_recursively_deletes(self):
        script = (ROOT / "installer" / "Lazarus-Agent.nsi").read_text(encoding="utf-8")
        self.assertIn("RequestExecutionLevel user", script)
        self.assertIn("SetShellVarContext current", script)
        self.assertIn('InstallDir "$LOCALAPPDATA\\Programs\\Lazarus Agent"', script)
        self.assertIn("SilentInstall normal", script)
        self.assertIn("SilentUnInstall normal", script)
        self.assertNotIn('RMDir /r "$INSTDIR"', script)
        self.assertIn('"${DATA_ROOT}\\temp"', script)
        self.assertIn('"/DELOUTPUT"', script)
        self.assertIn('StrCmp $DeleteOutput 1', script)
        self.assertNotIn('"${DATA_ROOT}\\jobs"', script)
        self.assertIn('"/CLEARCACHE"', script)

    def test_release_ci_installs_and_calls_the_known_nsis_compiler(self):
        workflow = (ROOT / ".github" / "workflows" / "release-windows.yml").read_text(encoding="utf-8")
        self.assertIn("choco install nsis --no-progress --yes", workflow)
        self.assertIn("NSIS\\makensis.exe", workflow)
        self.assertIn("& $nsis", workflow)
        self.assertIn("LAZARUS_UPDATE_PRIVATE_KEY: ${{ secrets.LAZARUS_UPDATE_PRIVATE_KEY }}", workflow)


if __name__ == "__main__":
    unittest.main()
