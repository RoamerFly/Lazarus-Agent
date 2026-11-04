import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

import apk_edit_build
from storage_paths import StoragePaths


class ApkEditBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.paths = StoragePaths(self.root / "storage").ensure()
        self.addCleanup(self.temp.cleanup)

    def test_rejects_invalid_or_unverified_cached_apktool(self):
        target = self.paths.runtime("apktool") / "apktool_2.12.1.jar"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"wrong")
        with patch("apk_edit_build.resolve_storage_paths", return_value=self.paths):
            self.assertFalse(apk_edit_build._validate_apktool(target))

    def test_explicit_apktool_requires_exact_hash(self):
        candidate = self.root / "candidate.jar"
        candidate.write_bytes(b"wrong")
        with patch.dict("apk_edit_build.os.environ", {"REVIVER_APKTOOL_JAR": str(candidate)}, clear=False):
            with self.assertRaisesRegex(apk_edit_build.ApkEditBuildError, "哈希"):
                apk_edit_build.apktool_jar()

    def test_download_checks_hash_before_publish(self):
        target = self.root / "apktool.jar"
        class Response:
            headers = {"Content-Range": "bytes 0-4/5"}
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, _): return b"wrong"
        with patch("apk_edit_build.APKTOOL_SIZE", 5), patch("apk_edit_build.APKTOOL_SHA256", "0" * 64), \
             patch("apk_edit_build.urlopen", return_value=Response()):
            with self.assertRaisesRegex(apk_edit_build.ApkEditBuildError, "校验"):
                apk_edit_build._download_apktool(target)
        self.assertFalse(target.exists())
        self.assertFalse(target.with_suffix(".part").exists())

    def test_direct_download_mode_does_not_use_global_urlopen(self):
        target = self.root / "apktool.jar"
        class Response:
            headers = {"Content-Range": "bytes 0-4/5"}
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, _): return b"wrong"
        class Opener:
            def open(self, request, timeout): return Response()
        with patch("apk_edit_build.APKTOOL_SIZE", 5), patch("apk_edit_build.APKTOOL_SHA256", "0" * 64), \
             patch.dict("apk_edit_build.os.environ", {"REVIVER_APKTOOL_DIRECT": "1"}, clear=False), \
             patch("apk_edit_build.build_opener", return_value=Opener()), \
             patch("apk_edit_build.urlopen", side_effect=AssertionError("system proxy should not be used")):
            with self.assertRaisesRegex(apk_edit_build.ApkEditBuildError, "校验"):
                apk_edit_build._download_apktool(target)
        self.assertFalse(target.exists())

    def test_build_requires_a_new_output_before_downloading_tools(self):
        source = self.root / "source.apk"
        source.write_bytes(b"apk")
        tools = self.root / "tools"
        (tools / "lib").mkdir(parents=True)
        (tools / "zipalign.exe").write_bytes(b"x")
        (tools / "lib" / "apksigner.jar").write_bytes(b"x")
        with self.assertRaisesRegex(apk_edit_build.ApkEditBuildError, "尚不存在"):
            apk_edit_build.build_modified(source, source, 36, tools, lambda _: {"kind":"test"})

    def test_build_reports_hash_and_uses_ascii_staging(self):
        source = self.root / "中文目录" / "输入.apk"
        source.parent.mkdir()
        with ZipFile(source, "w") as archive:
            archive.writestr("AndroidManifest.xml", b"original")
        output = self.root / "输出" / "candidate.apk"
        tools = self.root / "tools"
        (tools / "lib").mkdir(parents=True)
        (tools / "zipalign.exe").write_bytes(b"x")
        (tools / "lib" / "apksigner.jar").write_bytes(b"x")
        jar = self.root / "apktool.jar"
        jar.write_bytes(b"jar")
        seen = []
        def fake_run(command, env=None, cwd=None):
            seen.append([str(x) for x in command])
            if "d" in command:
                Path(command[command.index("--output") + 1]).mkdir()
            elif "b" in command:
                Path(command[command.index("--output") + 1]).write_bytes(b"unsigned")
            elif "zipalign.exe" in str(command[0]):
                Path(command[-1]).write_bytes(b"aligned")
            elif "sign" in command:
                Path(command[command.index("--out") + 1]).write_bytes(b"signed")
            return ""
        with patch("apk_edit_build.apktool_jar", return_value=jar), patch("apk_edit_build.run", fake_run), \
             patch("apk_edit_build.test_key", return_value=self.root / "key.p12"), patch("apk_edit_build._java", return_value="java"):
            result = apk_edit_build.build_modified(source, output, 36, tools, lambda decoded: {"kind":"remember", "decoded": decoded.name})
        self.assertTrue(output.is_file())
        self.assertEqual(result["source_sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
        self.assertEqual(result["sha256"], hashlib.sha256(b"signed").hexdigest())
        self.assertTrue(all(all(ord(char) < 128 for char in item) for command in seen for item in command))

    def test_transform_must_have_kind(self):
        with self.assertRaisesRegex(apk_edit_build.ApkEditBuildError, "尚不存在"):
            apk_edit_build.build_modified(self.root / "none.apk", self.root / "none.apk", 36, self.root, lambda _: {})

    def test_unsafe_archive_is_rejected_before_tool_download(self):
        source = self.root / "bad.apk"
        with ZipFile(source, "w") as archive:
            archive.writestr("../outside", b"x")
        tools = self.root / "tools"
        (tools / "lib").mkdir(parents=True)
        (tools / "zipalign.exe").write_bytes(b"x")
        (tools / "lib" / "apksigner.jar").write_bytes(b"x")
        with patch("apk_edit_build.apktool_jar") as fetch:
            with self.assertRaisesRegex(apk_edit_build.ApkEditBuildError, "不安全"):
                apk_edit_build.build_modified(source, self.root / "output.apk", 36, tools, lambda _: {"kind":"test"})
        fetch.assert_not_called()

    def test_inspection_uses_disposable_ascii_decoded_tree(self):
        source = self.root / "source.apk"
        with ZipFile(source, "w") as archive:
            archive.writestr("AndroidManifest.xml", b"binary")
        jar = self.root / "apktool.jar"
        jar.write_bytes(b"jar")
        calls = []
        def fake_run(command, env=None, cwd=None):
            calls.append([str(value) for value in command])
            Path(command[command.index("--output") + 1]).mkdir()
            return ""
        with patch("apk_edit_build.apktool_jar", return_value=jar), patch("apk_edit_build.run", fake_run), \
             patch("apk_edit_build._java", return_value="java"):
            result = apk_edit_build.inspect_decoded(source, self.root / "not-needed", lambda tree: {"directory": tree.name})
        self.assertEqual(result["inspection"], {"directory": "decoded"})
        self.assertTrue(all(all(ord(char) < 128 for char in item) for command in calls for item in command))
