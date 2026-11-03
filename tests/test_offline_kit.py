from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import offline_kit
import reviver


class OfflineKitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.sdk = self.root / "sdk"
        self.images = self.root / "images"
        self.java = self.root / "java"
        (self.sdk / "platform-tools").mkdir(parents=True)
        (self.sdk / "platform-tools" / "adb.exe").write_bytes(b"adb")
        (self.sdk / "emulator").mkdir()
        (self.sdk / "emulator" / "emulator.exe").write_bytes(b"emulator")
        (self.sdk / "build-tools" / "36.0.0" / "lib").mkdir(parents=True)
        (self.sdk / "build-tools" / "36.0.0" / "zipalign.exe").write_bytes(b"zipalign")
        (self.sdk / "build-tools" / "36.0.0" / "lib" / "apksigner.jar").write_bytes(b"signer")
        (self.java / "bin").mkdir(parents=True)
        (self.java / "bin" / "java.exe").write_bytes(b"java")
        (self.java / "bin" / "keytool.exe").write_bytes(b"keytool")
        for api in offline_kit.APIS:
            image = self.images / "system-images" / f"android-{api}" / "default" / "x86_64"
            image.mkdir(parents=True)
            (image / "system.img").write_bytes(f"api-{api}".encode())

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_build_and_verify_uses_relative_manifest_paths(self) -> None:
        output = self.root / offline_kit.KIT_NAME
        result = offline_kit.build_kit(output, self.sdk, self.java, self.images)
        self.assertEqual(result["apis"], list(offline_kit.APIS))
        manifest = (output / "manifest.json").read_text(encoding="utf-8")
        self.assertNotIn(str(output), manifest)
        self.assertTrue(offline_kit.verify_kit(output)["ok"])
        (output / "android-sdk" / "platform-tools" / "adb.exe").write_bytes(b"changed")
        self.assertFalse(offline_kit.verify_kit(output)["ok"])

    def test_sibling_kit_wins_without_path_or_android_studio(self) -> None:
        program = self.root / "release"
        kit = program / offline_kit.KIT_NAME
        offline_kit.build_kit(kit, self.sdk, self.java, self.images)
        saved = dict(os.environ)
        try:
            os.environ["PATH"] = ""
            os.environ.pop("REVIVER_SDK", None)
            os.environ.pop("REVIVER_OFFLINE_KIT", None)
            os.environ.pop("JAVA_HOME", None)
            with patch.object(offline_kit, "_program_root", return_value=program):
                self.assertEqual(offline_kit.discover_sdk(), kit / "android-sdk")
                self.assertEqual(offline_kit.discover_java_home(), kit / "jre")
                env = offline_kit.java_environment({"PATH": ""})
                self.assertEqual(env["JAVA_HOME"], str(kit / "jre"))
                self.assertTrue(env["PATH"].startswith(str(kit / "jre" / "bin")))
        finally:
            os.environ.clear()
            os.environ.update(saved)

    def test_portable_storage_never_copies_legacy_signing_key(self) -> None:
        local = self.root / "local"
        legacy = local / "LegacyApkReviver" / "signing"
        legacy.mkdir(parents=True)
        (legacy / "lab.p12").write_bytes(b"private-key-fixture")
        (legacy / "lab.pass").write_text("secret", encoding="ascii")
        portable = self.root / "portable-output"
        saved = dict(os.environ)
        try:
            os.environ["LOCALAPPDATA"] = str(local)
            os.environ["LAZARUS_STORAGE_ROOT"] = str(portable)
            os.environ["LAZARUS_PORTABLE"] = "1"
            selected = reviver._signing_home()
            self.assertEqual(selected, portable / "data" / "signing")
            self.assertFalse((selected / "lab.p12").exists())
            os.environ.pop("LAZARUS_STORAGE_ROOT", None)
            os.environ.pop("LAZARUS_PORTABLE", None)
            self.assertEqual(reviver._signing_home(), legacy)
        finally:
            os.environ.clear()
            os.environ.update(saved)


if __name__ == "__main__":
    unittest.main()
