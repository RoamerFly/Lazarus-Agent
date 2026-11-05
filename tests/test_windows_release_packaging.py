import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tools import verify_release_candidate


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

    def test_publication_binds_new_regression_to_build_and_keeps_historical_gate(self):
        version, build, commit = "0.6.17", "1234", "a" * 40
        archived = ROOT / "docs/validation/f01-p1c"
        with TemporaryDirectory() as directory:
            temporary = Path(directory)
            validation = temporary / "docs/validation"
            shutil.copytree(archived, validation / "f01-p1c")
            with patch.object(verify_release_candidate, "ROOT", temporary):
                verify_release_candidate.require_p1c("0.6.16", "unused", "unused")
                with self.assertRaisesRegex(ValueError, "发布回归"):
                    verify_release_candidate.require_p1c(version, build, commit)

                current = validation / "f01-release-regression" / version
                shutil.copytree(archived, current)
                result = json.loads((current / "results.json").read_bytes())
                result.update(app_version=version, implementation_commit=commit, source_build_run=build,
                              evaluation_role=verify_release_candidate.REGRESSION_ROLE)
                (current / "results.json").write_text(json.dumps(result), encoding="utf-8")
                verify_release_candidate.require_p1c(version, build, commit)
                with self.assertRaisesRegex(ValueError, "候选签名构建"):
                    verify_release_candidate.require_p1c(version, "5678", commit)


if __name__ == "__main__":
    unittest.main()
