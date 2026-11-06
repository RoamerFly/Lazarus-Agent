from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from tools import verify_review_package
from tools.verify_review_package import verify


class ReviewPackageTests(unittest.TestCase):
    def test_release_uses_distinct_source_archive_and_implementation_commits(self):
        source_commit, implementation = "a" * 40, "b" * 40
        release = {"schema": 1, "version": "0.6.18", "assets": {
            "windows-x64-portable-exe": {"name": "Agent.exe", "url": "https://example.org/Agent.exe",
                                         "sha256": "c" * 64},
        }}
        status = {"app_version": "0.6.18", "source_commit": source_commit,
                  "release_source_commit": implementation}
        identity = {"input_sha256": "d" * 64, "candidate_sha256": "e" * 64}
        video = b"video fixture"
        for archive_commit, demo_commit, reason in (
            (source_commit, implementation, None),
            (implementation, implementation, "源码归档的提交"),
            (source_commit, source_commit, "演示录制的版本或源码"),
        ):
            with self.subTest(archive_commit=archive_commit, demo_commit=demo_commit):
                source = BytesIO()
                with ZipFile(source, "w") as code:
                    code.comment = archive_commit.encode()
                    code.writestr("app_version.py", 'APP_VERSION = "0.6.18"\n')
                demo = {"app_version": "0.6.18", "video_sha256": sha256(video).hexdigest(),
                        "capture": {**identity, "app_version": "0.6.18", "source_commit": demo_commit}}
                files = {
                    "Windows桌面版/latest.json": json.dumps(release).encode(),
                    "Windows桌面版/latest.json.sig": b"signature fixture",
                    "F01-源代码.zip": source.getvalue(),
                    "遗留软件复活智能体演示.mp4": video,
                    "演示记录.json": json.dumps(demo).encode(),
                    "可分发样本/工作台演示/agent-acceptance.json": json.dumps(identity).encode(),
                }
                checksums = {name: sha256(data).hexdigest() for name, data in files.items()}
                with ZipFile(BytesIO(), "w") as archive, patch.object(verify_review_package, "verify_manifest"):
                    for name, data in files.items():
                        archive.writestr(name, data)
                    if reason:
                        with self.assertRaisesRegex(ValueError, reason):
                            verify_review_package.check_signed_release(archive, checksums, status)
                    else:
                        self.assertEqual(verify_review_package.check_signed_release(archive, checksums, status),
                                         {"version": "0.6.18", "source_commit": implementation})

    def test_counterexample_device_versions_must_match_target(self):
        base = b"test APK input"
        candidate = b"test APK output"
        identity = {"input_sha256": sha256(base).hexdigest(),
                    "candidate_sha256": sha256(candidate).hexdigest()}
        visible = {"action": "expect", "value": "Ready"}
        hidden = {"action": "absent", "value": "Ready"}
        success = {**visible, "status": "passed", "match_count": 1}
        failure = {**hidden, "status": "failed", "match_count": 1}

        def acceptance(row_api, device_api, *, counterexample=False):
            status = "failed" if counterexample else "behavior-verified"
            return {
                **identity, "targets": [36],
                "status": "incomplete" if counterexample else status,
                "steps": [visible, hidden] if counterexample else [visible],
                "rows": [{
                    "api": row_api, "status": status, "candidate": {
                        "install": "passed", "launch": "passed",
                        "function": "failed" if counterexample else "passed",
                        "steps": [success, failure] if counterexample else [success],
                        "device": {"api": device_api, "installed_sha256": identity["candidate_sha256"]},
                    },
                }],
            }

        with TemporaryDirectory() as directory:
            for row_api, device_api in ((36, 36), (35, 36), (36, 35)):
                with self.subTest(row_api=row_api, device_api=device_api):
                    archive = Path(directory) / f"{row_api}-{device_api}.zip"
                    files = {"复核状态.json": b"{}"}
                    for folder, report in (
                        ("可分发样本/P01", acceptance(36, 36)),
                        ("反例样本/N03", acceptance(row_api, device_api, counterexample=True)),
                    ):
                        files[f"{folder}/base.apk"] = base
                        files[f"{folder}/candidate.apk"] = candidate
                        files[f"{folder}/agent-acceptance.json"] = json.dumps(report).encode()
                    files["反例样本/N03/expected-outcome.json"] = json.dumps({
                        "exit_code": 1, "steps": [success, failure],
                    }).encode()
                    with ZipFile(archive, "w") as output:
                        for name, data in files.items():
                            output.writestr(name, data)
                        output.writestr("SHA256SUMS.txt", "".join(
                            f"{sha256(data).hexdigest()}  {name}\n" for name, data in files.items()))
                    if row_api == device_api == 36:
                        self.assertEqual(len(verify(archive)["counterexamples"]), 1)
                    else:
                        with self.assertRaisesRegex(ValueError, "设备版本"):
                            verify(archive)
