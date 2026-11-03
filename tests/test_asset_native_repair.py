"""Focused offline checks for the fail-closed embedded-native mapper."""

from pathlib import Path
import sys
import unittest
from zipfile import ZIP_DEFLATED, ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from asset_native_repair import replacement_map


ROOT = Path(__file__).resolve().parents[1]


class ReplacementMapTests(unittest.TestCase):
    def test_pacific_rim_proxy_payloads_are_identified(self):
        apk = ROOT / "old_apk" / "环太平洋.apk"
        if not apk.exists():
            self.skipTest("Pacific Rim fixture is not available")
        self.assertEqual(
            replacement_map(apk),
            {
                "lib/armeabi-v7a/libmono.so": "assets/libs/armeabi-v7a/libmono.so",
                "lib/armeabi-v7a/libunity.so": "assets/libs/armeabi-v7a/libunity.so",
            },
        )

    def test_existing_non_payload_apk_is_a_negative_case(self):
        apk = ROOT / "old_apk" / "OI-Notepad-1.3.apk"
        if not apk.exists():
            self.skipTest("OI Notepad fixture is not available")
        self.assertEqual(replacement_map(apk), {})

    def test_proxy_without_exact_nul_terminated_payload_reference_is_rejected(self):
        source = ROOT / "old_apk" / "环太平洋.apk"
        if not source.exists():
            self.skipTest("Pacific Rim fixture is not available")
        top_name = "lib/armeabi-v7a/libmono.so"
        payload_name = "assets/libs/armeabi-v7a/libmono.so"
        needle = payload_name.encode("ascii") + b"\0"
        fixture = ROOT / "tests" / "_missing-direct-reference.apk"
        try:
            with ZipFile(source) as original:
                top = original.read(top_name)
                self.assertIn(needle, top)
                # Keep the ELF layout, SONAME and exports untouched while
                # invalidating only the required embedded-payload pathname.
                top = top.replace(needle, needle[:-1] + b"X", 1)
                payload = original.read(payload_name)
            with ZipFile(fixture, "w", ZIP_DEFLATED) as archive:
                archive.writestr(top_name, top)
                archive.writestr(payload_name, payload)
            self.assertEqual(replacement_map(fixture), {})
        finally:
            fixture.unlink(missing_ok=True)

    def test_duplicate_zip_member_fails_closed(self):
        fixture = ROOT / "tests" / "_duplicate-native-member.apk"
        try:
            with ZipFile(fixture, "w", ZIP_DEFLATED) as archive:
                archive.writestr("lib/armeabi-v7a/libx_proxy.so", b"not an elf")
                archive.writestr("lib/armeabi-v7a/libx_proxy.so", b"still not an elf")
            self.assertEqual(replacement_map(fixture), {})
        finally:
            fixture.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
