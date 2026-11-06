from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from tools import package_private_handoff, review_archive, review_evidence, verify_review_package
from tools.verify_review_package import verify


class ReviewPackageTests(unittest.TestCase):
    def test_review_archive_preflight_binds_existing_workspace_to_source_bytes(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            package, workspace, exe = root / "package", root / "workspace", root / "Lazarus-Agent.exe"
            package.mkdir()
            exe.write_bytes(b"portable test build")
            build = {"version": "0.6.29", "source_commit": "a" * 40,
                     "exe_sha256": sha256(exe.read_bytes()).hexdigest(), "self_test_exit": 0}
            (package / "本地构建回执.json").write_text(json.dumps(build), encoding="utf-8")
            source = package / "F01-源代码-v0.6.29.zip"
            with ZipFile(source, "w") as archive:
                archive.comment = build["source_commit"].encode()
                archive.writestr("app_version.py", 'APP_VERSION = "0.6.29"')
            receipt = {**build, "app_version": build["version"],
                       "source_archive_sha256": sha256(source.read_bytes()).hexdigest()}
            (package / "同版材料校验回执.json").write_text(json.dumps(receipt), encoding="utf-8")
            self.assertEqual(review_archive.prepare_workspace(package, workspace, exe), build)
            self.assertEqual(review_archive.prepare_workspace(package, workspace, exe), build)
            (workspace / "app_version.py").write_text('APP_VERSION = "0.6.29-dev"', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "工作区源码已变化"):
                review_archive.prepare_workspace(package, workspace, exe)

    def test_review_source_rejects_unsafe_and_case_colliding_members(self):
        for names in (("src/../outside.py",), ("src/CON.txt",), ("build/replay.py",),
                      ("tools/Run.py", "tools/run.py")):
            with self.subTest(names=names), ZipFile(BytesIO(), "w") as archive:
                for name in names:
                    archive.writestr(name, "test")
                with self.assertRaisesRegex(ValueError, "不安全或重复"):
                    review_archive.source_members(archive)

    def test_private_handoff_rejects_an_external_shared_observation(self):
        target, caller = package_private_handoff.TARGET, package_private_handoff.CALLER
        apk, probe = "a" * 64, "b" * 64
        rejected = {"outcome": "target-rejected", "observed": [[target, "Rejected"]]}
        run = {**rejected, "api": 36, "abis": ["x86_64"], "candidate_and_installed_sha256": apk,
               "caller_apk_sha256": probe, "uids": {target: 10000, caller: 10001}, "before_share": rejected,
               "in_app_share": [{"action": "cold_start", "status": "passed"},
                                {"action": "tap", "value": "Share local record", "status": "passed"},
                                {"action": "expect", "value": "Shared", "status": "passed", "match_count": 1}]}
        fixed = {**run, "record_uri": f"content://{package_private_handoff.DEFAULT_AUTHORITY}/record.txt",
                 "forged_bytes_after": rejected}
        authority = package_private_handoff.FOREIGN_PROVIDER
        foreign = {**run, "record_uri": authority + "/record", "alternate_foreign_uri": authority + "/other",
                   "alternate_foreign_value": rejected}
        proof = {"status": "boundary-verified", "candidate_sha256": apk, "network_model_calls": 0,
                 "fixed_probe_uri": fixed, "foreign_provider": foreign}
        self.assertEqual(package_private_handoff.verify_boundary(proof, apk, probe), 6)
        foreign["observed"] = [(target, "Rejected")]
        self.assertEqual(package_private_handoff.verify_boundary(proof, apk, probe), 6)
        foreign["observed"] = [[target, "Rejected"], [target, "Shared"]]
        with self.assertRaisesRegex(ValueError, "未被接收端"):
            package_private_handoff.verify_boundary(proof, apk, probe)

    def test_supplemental_windows_receipt_rejects_a_different_executable(self):
        path = Path(__file__).resolve().parents[1] / "docs/validation/0.6.29-cold-runtime.json"
        receipt = json.loads(path.read_bytes())
        build = {key: receipt[key] for key in ("version", "source_commit", "exe_sha256")}
        self.assertEqual(review_evidence.verify_runtime(receipt, build)["components"], 5)
        receipt["exe_sha256"] = "a" * 64
        with self.assertRaisesRegex(ValueError, "Windows 首次下载"):
            review_evidence.verify_runtime(receipt, build)

    def test_supplemental_report_checks_device_readback(self):
        case = {"id": "P01", "steps": [["expect", "expected text"]]}
        row = {"trial": "P01-agent-1", "source_sha256": "a" * 64, "candidate_sha256": "b" * 64}
        observed = {"api": 36, "baseline": {"install": "failed"}, "candidate": {
            "install": "passed", "device": {"api": 36, "abis": ["x86_64"], "installed_sha256": "c" * 64},
        }}
        report = {"input_sha256": row["source_sha256"], "candidate_sha256": row["candidate_sha256"],
                  "targets": [36], "steps": [{"action": "expect", "value": "expected text"}], "rows": [observed]}
        with self.assertRaisesRegex(ValueError, "设备安装回读"):
            review_evidence.verify_device_report(report, case, row, 36)

    def test_empty_storage_preparation_requires_current_binary_and_all_downloads(self):
        identity = {"version": "0.6.28", "source_commit": "a" * 40, "exe_sha256": "b" * 64}
        scope = "same-exe-empty-storage-runtime"
        status = {**identity, "mode": "offline-trace-development-samples", "offline_cold_runtime": scope}
        components = sorted(verify_review_package.COLD_COMPONENTS)
        runtime = {"status": "ready", "version": identity["version"], "api": 36,
                   "terms_accepted": True, "missing": [], "reuse_without_download": True,
                   "components": dict.fromkeys(components, True)}
        downloads = {name: {"current": 1, "total": 1} for name in components}
        started = {"request_started": True, "status": "ready", "ready": True, "missing": [],
                   "reuse_without_download": True, "verified": components, "before_missing": components, "downloads": downloads,
                   "invalid_requests": [{"value": value, "http": 400} for value in
                                        ('false', '"false"', '"true"', '1', '[true]', '{"checked":true}', 'null')]}
        install = {**identity, "installed_exe_sha256": identity["exe_sha256"], "install_exit": 0,
                   "installed_self_test_exit": 0, "uninstall_exit": 0, "isolated_registration_removed": True,
                   "exported_output_preserved": True}
        valid = {**identity, "schema": 1, "status": scope, "windows": "Windows Server 2022", "api": 36,
                 "network_model_calls": 0, "preparation": started, "runtime": runtime, "install": install,
                 "interactive_desktop_verified": False, "device_steps_on_guest": 0}
        cases = (
            ({}, True),
            ({"exe_sha256": "c" * 64}, False),
            ({"preparation": {**started, "downloads": {key: value for key, value in downloads.items()
                                                        if key != "java"}}}, False),
            ({"runtime": {**runtime, "reuse_without_download": False}}, False),
            ({"interactive_desktop_verified": True}, False),
        )
        name = verify_review_package.COLD_WINDOWS_FILE
        for changes, accepted in cases:
            with self.subTest(changes=changes), ZipFile(BytesIO(), "w") as archive:
                archive.writestr("本地构建回执.json", json.dumps({**identity, "self_test_exit": 0}))
                archive.writestr(name, json.dumps({**valid, **changes}))
                check = verify_review_package.check_offline_cold_runtime
                if accepted:
                    self.assertEqual(check(archive, {name: "checksum"}, status)["components"], components)
                else:
                    with self.assertRaisesRegex(ValueError, "首次准备"):
                        check(archive, {name: "checksum"}, status)

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

    def test_counterexample_requires_the_recorded_failure_stage(self):
        digest = "a" * 64
        install = {"exit": 1, "output": "INSTALL_FAILED_NO_MATCHING_ABIS"}
        candidate = {"install": "failed", "launch": "not-run", "function": "not-run", "steps": [],
                     "device": {"api": 36, "abis": ["x86_64"], "installed_sha256": None, "install": install}}
        report = {"input_sha256": digest, "candidate_sha256": digest, "status": "incomplete",
                  "targets": [36], "rows": [{"api": 36, "status": "failed", "candidate": candidate}],
                  "steps": [{"action": "tap", "value": "Run"}, {"action": "expect", "value": "OK"}]}
        expected = {"kind": "missing-abi", "exit_code": 1, "steps": []}
        check = verify_review_package.verify_counterexample
        check("N01", report, expected, digest, digest)
        for output in ("INSTALL_FAILED_DEPRECATED_SDK_VERSION", "INSTALL_FAILED_INVALID_APK"):
            with self.subTest(output=output):
                install["output"] = output
                with self.assertRaises(ValueError):
                    check("N01", report, expected, digest, digest)

        step = {"action": "expect", "value": "Code"}
        missing = {**step, "status": "failed", "match_count": 0}
        expected = {"kind": "missing-information", "exit_code": 1, "steps": [missing.copy()]}
        report["steps"] = [step]
        candidate.update(install="passed", launch="passed", function="failed", steps=[missing])
        candidate["device"]["installed_sha256"] = digest
        check("N02", report, expected, digest, digest)
        missing["match_count"] = 1
        with self.assertRaises(ValueError):
            check("N02", report, expected, digest, digest)

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
