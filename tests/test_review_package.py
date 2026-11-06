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
    def test_local_regression_binds_build_and_counterexample_report(self):
        version, commit, exe_hash = "0.6.22", "a" * 40, "b" * 64
        audited = {"app_version": version, "implementation_commit": commit,
                   "source_build_run": "local:" + exe_hash, "protocol_sha256": "c" * 64,
                   "terminal_trials": 42, "counts": {}, "source_apk_sha256": {"N03": "d" * 64}}
        status = {"mode": "local-development-replay", "version": version, "source_commit": commit,
                  "exe_sha256": exe_hash, "protocol_sha256": audited["protocol_sha256"],
                  "terminal_trials": 42, "counts": {}, "distributable_agent_trials": ["P01-agent-1"],
                  "counterexamples": ["N03-agent-1"]}
        receipt = {"version": version, "source_commit": commit, "exe_sha256": exe_hash, "self_test_exit": 0}
        row = {"case": "N03", "state": "rejected", "candidate_sha256": "e" * 64,
               "raw_acceptance_sha256": "f" * 64}
        prefix = "反例样本/N03-agent-1/"
        files = {prefix + "base.apk": "d" * 64, prefix + "candidate.apk": "e" * 64,
                 prefix + "agent-acceptance.json": "f" * 64, prefix + "expected-outcome.json": "0" * 64}
        with (ZipFile(BytesIO(), "w") as archive,
              patch.object(verify_review_package, "check_regression_distribution",
                           return_value=({"N03-agent-1": row}, ["P01-agent-1"], []))):
            archive.writestr("本地构建回执.json", json.dumps(receipt))
            archive.writestr("results.json", json.dumps(audited))
            archive.writestr("diff-review.json", "{}")
            check = verify_review_package.check_local_regression
            self.assertEqual(check(archive, files, status)["distributed"], 1)
            with self.assertRaisesRegex(ValueError, "反例"):
                check(archive, {**files, prefix + "agent-acceptance.json": "0" * 64}, status)
            with self.assertRaisesRegex(ValueError, "EXE 回执"):
                check(archive, files, {**status, "exe_sha256": "0" * 64})

    def test_regression_distribution_binds_approved_trial_and_archive_hashes(self):
        version = "0.6.18"
        implementation, freeze = "a" * 40, "b" * 40
        input_hash, candidate_hash, proof_hash, diff_hash = ("c" * 64, "d" * 64, "e" * 64, "f" * 64)
        prefix = f"v{version} 发布回归/"
        reports = f"docs/validation/f01-release-regression/{version}/"
        protocol = b"{}"
        plan = {"app_version": version, "implementation_commit": implementation,
                "purpose": "development-regression-on-seen-cases", "build_run": "run",
                "source_protocol_freeze_commit": freeze, "protocol_sha256": sha256(protocol).hexdigest(),
                "planned_trials": 2}
        regression = {"app_version": version, "implementation_commit": implementation,
                      "evaluation_role": plan["purpose"], "source_build_run": plan["build_run"],
                      "freeze_commit": freeze, "protocol_sha256": plan["protocol_sha256"],
                      "planned_trials": 2, "terminal_trials": 2, "joint_gate": False, "counts": {}}
        rows = [{"case": "P01", "trial": f"P01-agent-{i}", "mode": "agent", "terminal": True,
                 "state": "passed", "candidate_sha256": candidate_hash, "raw_diff_sha256": diff_hash,
                 "raw_acceptance_sha256": proof_hash} for i in (1, 2)]
        review = {"rows": [{**row, "scope": "tested", "review": "checked", "distributable": i == 0}
                           for i, row in enumerate(rows)]}
        status = {"app_version": version, "release_regression": {
            **regression, "excluded_from_distribution": ["P01-agent-2"],
        }}
        approved = f"可分发样本/发布回归-v{version}/P01-agent-1/"
        files = {approved + "base.apk": input_hash, approved + "candidate.apk": candidate_hash,
                 approved + "agent-acceptance.json": proof_hash,
                 prefix + "f01-p1c-cases.json": plan["protocol_sha256"]}
        with ZipFile(BytesIO(), "w") as archive:
            archive.writestr(prefix + "plan.json", json.dumps(plan))
            archive.writestr(reports + "results.json", json.dumps({
                **regression, "source_apk_sha256": {"P01": input_hash}, "rows": rows,
            }))
            archive.writestr(reports + "diff-review.json", json.dumps(review))
            check = verify_review_package.check_release_regression
            self.assertEqual(check(archive, files, status)["distributed"], 1)
            with self.assertRaisesRegex(ValueError, "批准的样本"):
                check(archive, {**files, approved.replace("-1/", "-2/") + "candidate.apk": candidate_hash},
                      status)
            with self.assertRaisesRegex(ValueError, "审计哈希"):
                check(archive, {**files, approved + "agent-acceptance.json": "0" * 64}, status)

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
