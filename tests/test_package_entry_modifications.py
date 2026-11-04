"""The review package may include only the hash-pinned v0.4 modification evidence."""
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
import package_entry


class PackageEntryModificationTests(unittest.TestCase):
    def make_case(self, root, name, kind, artifact):
        path=root/artifact
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes((name+kind).encode("utf-8"))
        return {
            "name":name,
            "kind":kind,
            "artifact":artifact,
            "sha256":hashlib.sha256(path.read_bytes()).hexdigest(),
        }

    def test_accepts_two_hash_pinned_exports_and_uses_known_input_samples(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw)
            for relative in package_entry.DEFAULT_MODIFICATION_INPUTS.values():
                sample=root/relative
                sample.parent.mkdir(parents=True,exist_ok=True)
                sample.write_bytes(b"input")
            cases=[
                self.make_case(root,"记住文件","remember-file","artifacts/remember.apk"),
                self.make_case(root,"简体中文","localize-zh","artifacts/localize.apk"),
            ]
            report=root/"docs/validation/0.4.0-modifications.json"
            report.parent.mkdir(parents=True)
            report.write_text(__import__("json").dumps({"cases":cases}),encoding="utf-8")
            with patch.object(package_entry,"ROOT",root):
                returned, verified=package_entry.validated_modification_cases()
            self.assertEqual(returned,report)
            self.assertEqual([case[0] for case in verified],["记住文件","简体中文"])
            self.assertEqual(verified[0][1],root/package_entry.DEFAULT_MODIFICATION_INPUTS["remember-file"])

    def test_rejects_an_export_when_its_declared_hash_does_not_match(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw)
            for relative in package_entry.DEFAULT_MODIFICATION_INPUTS.values():
                sample=root/relative
                sample.parent.mkdir(parents=True,exist_ok=True)
                sample.write_bytes(b"input")
            case=self.make_case(root,"记住文件","remember-file","artifacts/remember.apk")
            case["sha256"]="0"*64
            second=self.make_case(root,"简体中文","localize-zh","artifacts/localize.apk")
            report=root/"docs/validation/0.4.0-modifications.json"
            report.parent.mkdir(parents=True)
            report.write_text(__import__("json").dumps({"cases":[case,second]}),encoding="utf-8")
            with patch.object(package_entry,"ROOT",root):
                with self.assertRaisesRegex(ValueError,"哈希不匹配"):
                    package_entry.validated_modification_cases()


if __name__ == "__main__":
    unittest.main()
