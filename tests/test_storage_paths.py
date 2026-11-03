from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest

from storage_paths import (
    APP_NAME,
    STORAGE_ROOT_ENV,
    configure_environment,
    migrate_legacy_data,
    resolve_storage_paths,
)


class StoragePathsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_install_root_uses_user_data_directory(self) -> None:
        paths = resolve_storage_paths({"LOCALAPPDATA": str(self.root / "local")})
        self.assertEqual(paths.root, (self.root / "local" / APP_NAME).resolve())
        self.assertEqual(paths.jobs, paths.root / "data" / "jobs")

    def test_portable_flag_keeps_data_beside_program(self) -> None:
        program = self.root / "portable-release"
        program.mkdir()
        (program / "portable.flag").touch()
        paths = resolve_storage_paths({}, program_root=program)
        self.assertEqual(paths.root, program.resolve())
        self.assertEqual(paths.output, program / "output")

    def test_explicit_storage_root_has_highest_priority(self) -> None:
        selected = self.root / "chosen"
        paths = resolve_storage_paths(
            {"LOCALAPPDATA": str(self.root / "local"), STORAGE_ROOT_ENV: str(selected)},
            program_root=self.root,
        )
        self.assertEqual(paths.root, selected.resolve())

    def test_existing_old_install_is_used_in_place_not_copied(self) -> None:
        local = self.root / "local"
        legacy_jobs = local / "LegacyApkReviver" / "jobs"
        legacy_jobs.mkdir(parents=True)
        paths = resolve_storage_paths({"LOCALAPPDATA": str(local)})
        self.assertTrue(paths.legacy_layout)
        self.assertEqual(paths.root, legacy_jobs.parent)
        self.assertEqual(paths.jobs, legacy_jobs)

    def test_build_venv_does_not_hide_an_existing_old_install(self) -> None:
        local = self.root / "local"
        (local / APP_NAME / "env" / "build-venv").mkdir(parents=True)
        (local / "LegacyApkReviver" / "jobs").mkdir(parents=True)
        paths = resolve_storage_paths({"LOCALAPPDATA": str(local)})
        self.assertTrue(paths.legacy_layout)

    def test_legacy_migration_only_copies_missing_files(self) -> None:
        local = self.root / "local"
        old = local / "LegacyApkReviver"
        (old / "jobs" / "old-task").mkdir(parents=True)
        (old / "jobs" / "old-task" / "state.json").write_text('{"old": true}', encoding="utf-8")
        (old / "desktop.log").write_text("old log", encoding="utf-8")
        paths = resolve_storage_paths({
            "LOCALAPPDATA": str(local),
            STORAGE_ROOT_ENV: str(local / APP_NAME),
        })
        paths.ensure()
        (paths.jobs / "old-task").mkdir()
        (paths.jobs / "old-task" / "state.json").write_text('{"new": true}', encoding="utf-8")

        report = migrate_legacy_data(paths, {"LOCALAPPDATA": str(local)})

        self.assertEqual(json.loads((paths.jobs / "old-task" / "state.json").read_text(encoding="utf-8")), {"new": True})
        self.assertEqual((paths.logs / "legacy-desktop.log").read_text(encoding="utf-8"), "old log")
        self.assertEqual(report["mode"], "copy-only-no-overwrite")
        self.assertTrue((old / "jobs" / "old-task" / "state.json").exists())

    def test_configure_environment_preserves_explicit_jobs_override(self) -> None:
        selected_root = self.root / "storage"
        explicit_jobs = self.root / "external-jobs"
        before_storage = os.environ.get(STORAGE_ROOT_ENV)
        before_jobs = os.environ.get("REVIVER_JOBS")
        try:
            os.environ[STORAGE_ROOT_ENV] = str(selected_root)
            os.environ["REVIVER_JOBS"] = str(explicit_jobs)
            paths = configure_environment(migrate_legacy=False)
            self.assertTrue(paths.data.is_dir())
            self.assertTrue(paths.temp.is_dir())
            self.assertEqual(Path(os.environ["REVIVER_JOBS"]), explicit_jobs)
        finally:
            if before_storage is None:
                os.environ.pop(STORAGE_ROOT_ENV, None)
            else:
                os.environ[STORAGE_ROOT_ENV] = before_storage
            if before_jobs is None:
                os.environ.pop("REVIVER_JOBS", None)
            else:
                os.environ["REVIVER_JOBS"] = before_jobs

    def test_default_configuration_does_not_copy_legacy_jobs(self) -> None:
        local = self.root / "local"
        old_job = local / "LegacyApkReviver" / "jobs" / "large-history"
        old_job.mkdir(parents=True)
        (old_job / "evidence.bin").write_bytes(b"retain-in-place")
        selected_root = self.root / "new-root"
        saved = {name: os.environ.get(name) for name in ("LOCALAPPDATA", STORAGE_ROOT_ENV, "REVIVER_JOBS", "LAZARUS_MIGRATE_LEGACY")}
        try:
            os.environ["LOCALAPPDATA"] = str(local)
            os.environ[STORAGE_ROOT_ENV] = str(selected_root)
            os.environ.pop("REVIVER_JOBS", None)
            os.environ.pop("LAZARUS_MIGRATE_LEGACY", None)
            paths = configure_environment()
            self.assertFalse((paths.jobs / "large-history").exists())
            self.assertTrue((old_job / "evidence.bin").exists())
        finally:
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    def test_runtime_name_must_not_escape_storage(self) -> None:
        paths = resolve_storage_paths({STORAGE_ROOT_ENV: str(self.root)})
        with self.assertRaises(ValueError):
            paths.runtime("../outside")


if __name__ == "__main__":
    unittest.main()
