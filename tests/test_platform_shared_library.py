import unittest
from io import BytesIO
from pathlib import Path
import struct
from zipfile import ZipFile

from function_probe import discover_shared_library, resolve_platform_library
from function_probe import extract_missing_class
from runtime_patch import declare_shared_library, strings_and_append
from reviver import update_manifest


ROOT = Path(__file__).resolve().parents[1]


def dex_defining(class_name):
    """Tiny structurally sufficient DEX containing one class_def entry."""
    descriptor = ("L" + class_name.replace(".", "/") + ";").encode()
    string_data = bytes([len(descriptor)]) + descriptor + b"\0"
    string_off, type_off, class_off = 112, 116, 120
    data = bytearray(class_off + 32 + len(string_data))
    data[:8] = b"dex\n035\0"
    struct.pack_into("<III", data, 32, len(data), 112, 0x12345678)
    struct.pack_into("<IIII", data, 56, 1, string_off, 1, type_off)
    struct.pack_into("<II", data, 96, 1, class_off)
    struct.pack_into("<I", data, string_off, class_off + 32)
    struct.pack_into("<I", data, type_off, 0)
    struct.pack_into("<I", data, class_off, 0)
    data[class_off + 32:] = string_data
    return bytes(data)


def jar_defining(class_name):
    result = BytesIO()
    with ZipFile(result, "w") as archive:
        archive.writestr("classes.dex", dex_defining(class_name))
    return result.getvalue()


class PlatformSharedLibraryTests(unittest.TestCase):
    def test_resolver_requires_exact_device_library_evidence(self):
        libraries = "library:android.test.runner\nlibrary:android.test.base\nlibrary:android.test.mock\nlibrary:org.apache.http.legacy\n"
        self.assertEqual(resolve_platform_library("android.test.InstrumentationTestRunner", libraries),
                         "android.test.runner")
        self.assertEqual(resolve_platform_library("org.apache.http.ProtocolVersion", libraries),
                         "org.apache.http.legacy")
        self.assertEqual(resolve_platform_library("android.test.mock.MockContext", libraries), "android.test.mock")
        self.assertIsNone(resolve_platform_library("android.test.runner.AndroidTestRunner", libraries))
        self.assertIsNone(resolve_platform_library("android.test.InstrumentationTestRunner", "library:android.test.base"))

    def test_extract_missing_class_accepts_only_android_error_forms(self):
        self.assertEqual(extract_missing_class("NoClassDefFoundError: Failed resolution of: Landroid/test/InstrumentationTestRunner;"),
                         "android.test.InstrumentationTestRunner")
        self.assertEqual(extract_missing_class("ClassNotFoundException: Didn't find class \"android.test.mock.MockContext\""),
                         "android.test.mock.MockContext")
        self.assertIsNone(extract_missing_class("model says android.test.InstrumentationTestRunner"))

    def test_discovery_reads_class_definitions_and_requires_unique_owner(self):
        listing = "library:legacy.compat\n  file:/system/framework/legacy.compat.jar\n"
        calls = []
        def read_jar(path):
            calls.append(path)
            return jar_defining("com.example.Legacy")
        self.assertEqual(discover_shared_library("com.example.Legacy", listing, read_jar), "legacy.compat")
        self.assertEqual(calls, ["/system/framework/legacy.compat.jar"])

        duplicate = listing + "library:other.compat\n  file:/system/framework/other.compat.jar\n"
        self.assertIsNone(discover_shared_library("com.example.Legacy", duplicate,
                          lambda path: jar_defining("com.example.Legacy")))
        self.assertIsNone(discover_shared_library("com.example.Legacy", listing, lambda path: b"not a jar"))
        boot = "  framework ->  (jar) /system/framework/framework.jar\n"
        self.assertIsNone(discover_shared_library("com.example.Legacy", boot,
                          lambda path: self.fail("boot class path must not be inspected")))

    def test_known_library_fast_path_never_reads_jar(self):
        listing = "  android.test.runner ->  (jar) /system/framework/android.test.runner.jar\n"
        self.assertEqual(discover_shared_library("android.test.InstrumentationTestRunner", listing,
                         lambda path: self.fail("known mapping should not inspect JAR")), "android.test.runner")

    def test_manifest_patch_is_allow_list_and_detects_duplicate(self):
        fixture = ROOT / "fixtures/archive-reader/archive-reader-control.apk"
        with ZipFile(fixture) as archive:
            manifest = archive.read("AndroidManifest.xml")
        patched = declare_shared_library(manifest, "android.test.runner")
        self.assertNotEqual(manifest, patched)
        with self.assertRaisesRegex(ValueError, "already declared"):
            declare_shared_library(patched, "android.test.runner")
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            declare_shared_library(manifest, "arbitrary.library")
        extended = declare_shared_library(manifest, "legacy.compat", ["legacy.compat"])
        self.assertNotEqual(manifest, extended)

    def test_added_library_has_matching_raw_and_typed_string_indices(self):
        fixture = ROOT / "fixtures/archive-reader/archive-reader-broken.apk"
        with ZipFile(fixture) as archive:
            manifest = declare_shared_library(archive.read("AndroidManifest.xml"), "org.apache.http.legacy")
        offset = 8
        strings = []
        while offset < len(manifest):
            kind, header, size = struct.unpack_from("<HHI", manifest, offset)
            if kind == 1:
                strings, _, _ = strings_and_append(manifest[offset:offset + size], [])
            if kind == 0x102:
                name = struct.unpack_from("<I", manifest, offset + header + 4)[0]
                if strings[name] == "uses-library":
                    first = struct.unpack_from("<H", manifest, offset + header + 8)[0]
                    _, _, raw, _, _, value_type, typed = struct.unpack_from(
                        "<IIIHBBI", manifest, offset + header + first)
                    self.assertEqual((raw, value_type), (typed, 3))
                    self.assertEqual(strings[typed], "org.apache.http.legacy")
                    return
            offset += size
        self.fail("Patched manifest has no uses-library declaration")

    def test_manifest_composition_keeps_target_sdk_and_library(self):
        fixture = ROOT / "fixtures/archive-reader/archive-reader-control.apk"
        with ZipFile(fixture) as archive:
            manifest = archive.read("AndroidManifest.xml")
        # The fixture is deliberately old enough that a higher test target is
        # representable.  The second update refusing it proves the first patch
        # touched targetSdk rather than merely returning a changed byte stream.
        modern, before = update_manifest(manifest, 35)
        combined = declare_shared_library(modern, "android.test.runner")
        self.assertNotEqual(manifest, combined)
        self.assertGreaterEqual(before, 1)
        with self.assertRaisesRegex(ValueError, "already above"):
            update_manifest(combined, 35)
        with self.assertRaisesRegex(ValueError, "already declared"):
            declare_shared_library(combined, "android.test.runner")

