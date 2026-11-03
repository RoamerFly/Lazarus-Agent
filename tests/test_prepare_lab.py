from __future__ import annotations

from pathlib import Path
import hashlib
import os
import shutil
import tempfile
import sys
import types
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from tools import prepare_lab
from tools.prepare_lab import _extract_without_overwrite


class PrepareLabSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _zip(self, name: str, contents: dict[str, str]) -> Path:
        archive = self.root / name
        with ZipFile(archive, "w") as bundle:
            for member, text in contents.items():
                bundle.writestr(member, text)
        return archive

    def test_rejects_path_traversal_without_publishing_destination(self) -> None:
        archive = self._zip("unsafe.zip", {"../outside.txt": "no"})
        destination = self.root / "component"
        with self.assertRaises(ValueError):
            _extract_without_overwrite(archive, destination)
        self.assertFalse(destination.exists())
        self.assertFalse((self.root / "outside.txt").exists())

    def test_existing_component_is_never_overwritten(self) -> None:
        archive = self._zip("safe.zip", {"package/file.txt": "new"})
        destination = self.root / "component"
        destination.mkdir()
        sentinel = destination / "sentinel.txt"
        sentinel.write_text("keep", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            _extract_without_overwrite(archive, destination)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_successful_extraction_publishes_complete_tree(self) -> None:
        archive = self._zip("safe.zip", {"package/file.txt": "ready"})
        destination = self.root / "component"
        _extract_without_overwrite(archive, destination)
        self.assertEqual((destination / "package" / "file.txt").read_text(encoding="utf-8"), "ready")

    def test_inventory_is_local_only_and_reports_missing_components(self) -> None:
        report = prepare_lab.inventory(34, self.root / "runtime")
        self.assertFalse(report["ready"])
        self.assertTrue(report["networkRequired"])
        self.assertIn("system-image", report["missing"])
        self.assertEqual(report["payloadSize"], 0)

    def test_prepare_reuses_read_only_sources_and_activates_runtime(self) -> None:
        source = self.root / "source-sdk"
        (source / "platform-tools").mkdir(parents=True)
        (source / "platform-tools" / "adb.exe").write_bytes(b"adb")
        (source / "emulator").mkdir()
        (source / "emulator" / "emulator.exe").write_bytes(b"emulator")
        tools = source / "build-tools" / prepare_lab.BUILD_TOOLS_VERSION / "lib"
        tools.mkdir(parents=True)
        (tools.parent / "zipalign.exe").write_bytes(b"zipalign")
        (tools / "apksigner.jar").write_bytes(b"signer")
        image = source / "system-images" / "android-34" / "default" / "x86_64"
        image.mkdir(parents=True)
        (image / "system.img").write_bytes(b"image")
        java = self.root / "java"
        (java / "bin").mkdir(parents=True)
        (java / "bin" / "java.exe").write_bytes(b"java")
        (java / "bin" / "keytool.exe").write_bytes(b"keytool")

        def copy_link(src: Path, dest: Path) -> bool:
            shutil.copytree(src, dest)
            return True

        events: list[dict[str, object]] = []
        saved = dict(os.environ)
        try:
            with patch.object(prepare_lab, "_source_sdks", return_value=[source]), \
                 patch.object(prepare_lab, "discover_java_home", return_value=java), \
                 patch.object(prepare_lab, "_link_existing", side_effect=copy_link):
                result = prepare_lab.prepare_runtime(34, events.append, self.root / "runtime", timeout_seconds=20, terms_accepted=True)
        finally:
            os.environ.clear()
            os.environ.update(saved)
        self.assertTrue(result["ok"])
        self.assertEqual(result["installed"], [])
        self.assertEqual(set(result["reused"]), {"platform-tools", "emulator", "build-tools", "system-image", "java"})
        self.assertTrue(prepare_lab.inventory(34, self.root / "runtime")["ready"])
        self.assertIn("ready", {event["phase"] for event in events})

    def test_cancel_keeps_partial_download_for_later_resume(self) -> None:
        downloads = self.root / "downloads"
        downloads.mkdir()
        partial = downloads / "component.zip.part"
        partial.write_bytes(b"partial")
        spec = {"name": "component.zip", "url": "https://example.invalid/component.zip", "size": 10,
                "digest": hashlib.sha1(b"x" * 10).hexdigest(), "algorithm": "sha1"}
        with self.assertRaises(InterruptedError):
            prepare_lab._download(spec, downloads, None, "emulator", None, cancel=lambda: True)
        self.assertEqual(partial.read_bytes(), b"partial")

    def test_missing_component_requires_terms_before_network_download(self) -> None:
        with patch.object(prepare_lab, "_source_sdks", return_value=[]), \
             patch.object(prepare_lab, "discover_java_home", return_value=None), \
             self.assertRaises(PermissionError):
            prepare_lab.prepare_runtime(36, runtime_root=self.root / "empty-runtime", timeout_seconds=1)

    def test_refresh_updates_modules_that_imported_tool_constants(self) -> None:
        import bench_device
        sdk = self.root / "sdk"
        (sdk / "platform-tools").mkdir(parents=True)
        (sdk / "platform-tools" / "adb.exe").write_bytes(b"adb")
        (sdk / "emulator").mkdir()
        (sdk / "emulator" / "emulator.exe").write_bytes(b"emulator")
        tools = sdk / "build-tools" / "36.0.0" / "lib"
        tools.mkdir(parents=True)
        (tools.parent / "zipalign.exe").write_bytes(b"zipalign")
        (tools / "apksigner.jar").write_bytes(b"signer")
        consumer = types.ModuleType("workbench")
        consumer.ADB, consumer.TOOLS = "old-adb", "old-tools"
        prior = sys.modules.get("workbench")
        sys.modules["workbench"] = consumer
        saved = os.environ.get("REVIVER_SDK")
        old_sdk, old_adb, old_tools = bench_device.SDK, bench_device.ADB, bench_device.TOOLS
        try:
            bench_device.refresh_toolchain(sdk)
            self.assertEqual(consumer.ADB, sdk / "platform-tools" / "adb.exe")
            self.assertEqual(consumer.TOOLS, sdk / "build-tools" / "36.0.0")
        finally:
            if prior is None:
                sys.modules.pop("workbench", None)
            else:
                sys.modules["workbench"] = prior
            if saved is None:
                os.environ.pop("REVIVER_SDK", None)
            else:
                os.environ["REVIVER_SDK"] = saved
            bench_device.SDK, bench_device.ADB, bench_device.TOOLS = old_sdk, old_adb, old_tools


if __name__ == "__main__":
    unittest.main()
