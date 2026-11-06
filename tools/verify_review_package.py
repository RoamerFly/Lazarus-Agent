"""Verify review ZIP bytes and packaged APK proofs without extracting executable files."""
import argparse
import hashlib
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
import sys
from urllib.parse import unquote, urlsplit
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent_acceptance import verified_for
from updater import parse_manifest, verify_manifest

LOCAL_DEMO = "可分发样本/工作台演示/"


def check_local_links(archive, names):
    count = 0
    for name in names:
        if not name.endswith(".md"):
            continue
        for link in re.findall(r"\]\(([^\s)]+)\)", archive.read(name).decode("utf-8-sig")):
            parsed = urlsplit(link)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            target = posixpath.normpath(posixpath.join(posixpath.dirname(name), unquote(parsed.path)))
            if target not in names:
                raise ValueError(f"复核文档引用缺失文件 {name} → {target}")
            count += 1
    return count


def check_signed_release(archive, checksums, status):
    manifest_name = "Windows桌面版/latest.json"
    signature_name = manifest_name + ".sig"
    delivery = status.get("windows_delivery")
    commit = status.get("release_source_commit")
    if not (manifest_name in checksums or signature_name in checksums or commit or delivery):
        return None
    if manifest_name not in checksums or signature_name not in checksums:
        raise ValueError("复核包缺少完整的签名发布清单")
    raw = archive.read(manifest_name)
    verify_manifest(raw, archive.read(signature_name), ROOT / "update/lazarus-update-public.pem")
    release = parse_manifest(raw)
    if release.version != status.get("app_version"):
        raise ValueError("签名发布清单与复核包版本不一致")

    sources = [name for name in ("F01-源代码.zip", f"F01-源代码-v{release.version}.zip") if name in checksums]
    if sources or "源码版本.txt" in checksums:
        source_commit = status.get("source_commit", "")
        if len(sources) != 1 or not isinstance(source_commit, str) or not re.fullmatch(r"[a-f0-9]{40}", source_commit):
            raise ValueError("复核包缺少对应的源码归档或提交")
        with ZipFile(BytesIO(archive.read(sources[0]))) as source:
            if source.comment != source_commit.encode("ascii"):
                raise ValueError("源码归档的提交与复核包不一致")
            version_line = f'APP_VERSION = "{release.version}"'.encode("ascii")
            if version_line not in source.read("app_version.py").splitlines():
                raise ValueError("源码归档的程序版本与签名发布清单不一致")
        if ("源码版本.txt" in checksums
                and archive.read("源码版本.txt").decode("utf-8-sig").strip() != source_commit):
            raise ValueError("源码版本文件的提交与复核包不一致")

    if isinstance(delivery, dict):
        commit = commit or delivery.get("source_commit")
    elif delivery == "separate-local-installer":
        commit = commit or status.get("source_commit")
    regression = status.get("release_regression")
    if regression and (regression.get("app_version") != release.version
                       or (commit and regression.get("implementation_commit") != commit)):
        raise ValueError("发布回归与签名构建的版本或源码不一致")

    videos = [name for name in ("遗留软件复活智能体演示.mp4", "演示.mp4") if name in checksums]
    if videos or "演示记录.json" in checksums:
        if len(videos) != 1 or "演示记录.json" not in checksums:
            raise ValueError("复核包缺少对应的演示视频或录制回执")
        record = json.loads(archive.read("演示记录.json"))
        capture = record["capture"]
        if record.get("video_sha256") != checksums[videos[0]]:
            raise ValueError("演示视频与录制回执不一致")
        if (record.get("app_version") != release.version or capture.get("app_version") != release.version
                or (commit and capture.get("source_commit") != commit)):
            raise ValueError("演示录制的版本或源码与签名构建不一致")
        demo = "可分发样本/工作台演示"
        report = json.loads(archive.read(demo + "/agent-acceptance.json"))
        if (capture.get("candidate_sha256") != report.get("candidate_sha256")
                or capture.get("input_sha256") != report.get("input_sha256")):
            raise ValueError("演示 APK 与设备验收记录不一致")
    return {"version": release.version, "source_commit": commit}


PLAN_FIELDS = {"app_version": "app_version", "implementation_commit": "implementation_commit",
               "evaluation_role": "purpose", "source_build_run": "build_run",
               "freeze_commit": "source_protocol_freeze_commit", "protocol_sha256": "protocol_sha256",
               "planned_trials": "planned_trials"}


def regression_sample_hashes(audited, trial, folder):
    return {
        folder + "base.apk": audited["source_apk_sha256"][trial["case"]],
        folder + "candidate.apk": trial["candidate_sha256"],
        folder + "agent-acceptance.json": trial["raw_acceptance_sha256"],
    }


def check_regression_distribution(audited, review, checksums, prefix, distributed):
    rows = {row["trial"]: row for row in audited["rows"]}
    if (len(rows) != len(audited["rows"]) or len(rows) != audited["planned_trials"]
            or sum(row["terminal"] is True for row in rows.values()) != audited["terminal_trials"]
            or audited["terminal_trials"] != audited["planned_trials"]):
        raise ValueError("发布回归缺少完整且唯一的轮次终态")

    from tools.audit_f01_p1b import require_diff_review
    require_diff_review(audited, review)
    if any(type(row.get("distributable")) is not bool for row in review["rows"]):
        raise ValueError("发布回归的差异审查缺少明确的分发结论")
    excluded = sorted(row["trial"] for row in review["rows"] if not row["distributable"])
    approved = sorted(row["trial"] for row in review["rows"]
                      if row["distributable"] and rows[row["trial"]]["mode"] == "agent")
    expected = {}
    for trial in approved:
        expected.update(regression_sample_hashes(audited, rows[trial], f"{prefix}{trial}/"))
    if distributed != expected.keys():
        raise ValueError("发布回归的可分发目录与差异审查批准的样本不一致")
    if any(checksums[name] != digest for name, digest in expected.items()):
        raise ValueError("发布回归的可分发 APK 或验收报告与审计哈希不一致")
    return rows, approved, excluded


def check_release_regression(archive, checksums, status):
    regression = status.get("release_regression")
    distributed = {name for name in checksums if name.startswith("可分发样本/发布回归-v")}
    if regression is None and not distributed:
        return None
    version = status.get("app_version")
    if not regression or not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("缺少发布回归状态或版本")

    prefix = f"v{version} 发布回归/"
    reports = f"docs/validation/f01-release-regression/{version}/"
    plan = json.loads(archive.read(prefix + "plan.json"))
    audited = json.loads(archive.read(reports + "results.json"))
    review = json.loads(archive.read(reports + "diff-review.json"))
    summary_fields = (*PLAN_FIELDS, "terminal_trials", "joint_gate", "counts")
    if (regression["app_version"] != version
            or any(regression[key] != audited[key] for key in summary_fields)
            or any(regression[key] != plan[field] for key, field in PLAN_FIELDS.items())
            or regression["protocol_sha256"] != checksums.get(prefix + "f01-p1c-cases.json")):
        raise ValueError("发布回归的状态、方案与审计汇总不一致")
    # Check the declared exclusion list before comparing the distributed files.
    excluded_review = sorted(row["trial"] for row in review["rows"] if row.get("distributable") is False)
    if excluded_review != regression["excluded_from_distribution"]:
        raise ValueError("发布回归的分发排除清单与差异审查不一致")
    rows, approved, excluded = check_regression_distribution(
        audited, review, checksums, f"可分发样本/发布回归-v{version}/", distributed)
    return {"version": version, "trials": len(rows), "distributed": len(approved),
            "excluded_from_distribution": excluded}


def check_local_regression(archive, checksums, status):
    names = {"本地构建回执.json", "results.json", "diff-review.json"}
    if status.get("mode") != "local-development-replay" and not names <= checksums.keys():
        return None
    receipt = json.loads(archive.read("本地构建回执.json"))
    audited = json.loads(archive.read("results.json"))
    review = json.loads(archive.read("diff-review.json"))
    version, commit, exe_hash = status["version"], status["source_commit"], status["exe_sha256"]
    if (status.get("mode") != "local-development-replay" or version != receipt["version"]
            or version != audited["app_version"] or commit != receipt["source_commit"]
            or commit != audited["implementation_commit"] or exe_hash != receipt["exe_sha256"]
            or audited["source_build_run"] != "local:" + exe_hash or receipt["self_test_exit"] != 0
            or any(status[key] != audited[key] for key in ("protocol_sha256", "terminal_trials", "counts"))):
        raise ValueError("本地复测的 EXE 回执、版本或审计汇总不一致")
    if "plan.json" in checksums or "f01-p1c-cases.json" in checksums:
        plan = json.loads(archive.read("plan.json"))
        if (any(audited[key] != plan[field] for key, field in PLAN_FIELDS.items())
                or audited["protocol_sha256"] != checksums.get("f01-p1c-cases.json")):
            raise ValueError("本地复测的计划或冻结协议与审计汇总不一致")

    prefix = "可分发样本/"
    distributed = {name for name in checksums if name.startswith(prefix)
                   and not (status.get("local_review") and name.startswith(LOCAL_DEMO))}
    rows, approved, excluded = check_regression_distribution(
        audited, review, checksums, prefix, distributed)
    if approved != sorted(status["distributable_agent_trials"]):
        raise ValueError("本地复测的可分发清单与差异审查不一致")
    counterexample = "N03-agent-1"
    prefix = f"反例样本/{counterexample}/"
    expected = regression_sample_hashes(audited, rows[counterexample], prefix)
    if (status["counterexamples"] != [counterexample] or rows[counterexample]["state"] != "rejected"
            or {name for name in checksums if name.startswith("反例样本/")} !=
            expected.keys() | {prefix + "expected-outcome.json"}
            or any(checksums[name] != digest for name, digest in expected.items())):
        raise ValueError("本地复测的反例与审计轮次或哈希不一致")
    return {"version": version, "trials": len(rows), "distributed": len(approved),
            "excluded_from_distribution": excluded}


def check_local_review(archive, checksums, status, regression):
    edition = status.get("local_review")
    if edition is None:
        return None
    if (regression is None or edition != {"scope": "local-engineering-review",
                                          "clean_windows": "pending", "non_developer_trial": "pending"}):
        raise ValueError("本地评审包的范围、评测或独立复核状态不符")
    version, commit, exe_hash = status["version"], status["source_commit"], status["exe_sha256"]
    receipt = json.loads(archive.read("同版材料校验回执.json"))
    source_name = f"F01-源代码-v{version}.zip"
    material_hashes = {
        source_name: receipt["source_archive_sha256"],
        "遗留软件复活智能体方案说明.pdf": receipt["proposal_sha256"],
        "遗留软件复活智能体演示.mp4": receipt["video_sha256"],
    }
    if (receipt["app_version"] != version or receipt["source_commit"] != commit
            or receipt["exe_sha256"] != exe_hash
            or any(checksums.get(name) != digest for name, digest in material_hashes.items())):
        raise ValueError("本地评审包的方案、演示或源码与同版回执不一致")
    with ZipFile(BytesIO(archive.read(source_name))) as code:
        if (code.comment.decode("ascii") != commit
                or f'APP_VERSION = "{version}"'.encode("ascii") not in code.read("app_version.py").splitlines()):
            raise ValueError("本地评审包源码的提交或程序版本不一致")

    record = json.loads(archive.read("演示记录.json"))
    capture, demo = record["capture"], receipt["demo"]
    report = json.loads(archive.read(LOCAL_DEMO + "agent-acceptance.json"))
    screen = capture["device_screen"]
    demo_hashes = {
        LOCAL_DEMO + "base.apk": demo["input_sha256"],
        LOCAL_DEMO + "candidate.apk": demo["candidate_sha256"],
        LOCAL_DEMO + "agent-acceptance.json": demo["acceptance_sha256"],
        "演示设备画面.png": screen["sha256"],
    }
    if (record["app_version"] != version or record["video_sha256"] != receipt["video_sha256"]
            or record["duration_planned_seconds"] != receipt["video_seconds"]
            or capture["app_version"] != version or capture["source_commit"] != commit
            or demo["candidate_sha256"] != capture["candidate_sha256"]
            or demo["input_sha256"] != capture["input_sha256"]
            or demo["candidate_sha256"] != demo["installed_sha256"]
            or demo["candidate_sha256"] != screen["apk_sha256"] or demo["target_api"] != screen["api"]
            or demo["input_sha256"] != report["input_sha256"]
            or demo["passed_steps"] != len(report["steps"]) or report["targets"] != [demo["target_api"]]
            or {name for name in checksums if name.startswith(LOCAL_DEMO)} != demo_hashes.keys() - {"演示设备画面.png"}
            or any(checksums.get(name) != digest for name, digest in demo_hashes.items())
            or not verified_for(report, demo["candidate_sha256"])):
        raise ValueError("本地评审包的演示录制或设备验收证据不一致")

    for bundle_name, receipt_name in (
        (f"v{version}-Windows隔离安装复核工具.zip", "Windows安装复核工具回执.json"),
        (f"v{version}-归档阅读器专项复测输入-r3.zip", "归档复测环境回执.json"),
    ):
        windows = json.loads(archive.read(receipt_name))
        if (windows["version"] != version or windows["source_commit"] != commit
                or windows["exe_sha256"] != exe_hash or windows["bundle_sha256"] != checksums.get(bundle_name)):
            raise ValueError("本地评审包的 Windows 复核输入与回执不一致")
    return {"version": version, "source_commit": commit, "demo_steps": demo["passed_steps"], **edition}


def verify(path):
    with ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("复核包有重名条目")
        for name in names:
            parts = PurePosixPath(name).parts
            if name.startswith(("/", "\\")) or "\\" in name or ":" in name or ".." in parts:
                raise ValueError("复核包包含不安全路径")
        checksums = {}
        for row in archive.read("SHA256SUMS.txt").decode("utf-8").splitlines():
            checksum, name = row.split("  ", 1)
            if name in checksums or name not in names:
                raise ValueError("校验清单重复或引用缺失文件")
            checksums[name] = checksum
        if set(checksums) != set(names) - {"SHA256SUMS.txt"}:
            raise ValueError("文件与校验清单不完全对应")
        for name, expected in checksums.items():
            with archive.open(name) as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                    raise ValueError("文件哈希不一致：" + name)
        status = json.loads(archive.read("复核状态.json"))
        signed_release = check_signed_release(archive, checksums, status)
        release_regression = check_release_regression(archive, checksums, status)
        local_regression = check_local_regression(archive, checksums, status)
        local_review = check_local_review(archive, checksums, status, local_regression)
        samples = []
        for name in names:
            if "可分发样本" in PurePosixPath(name).parts and name.endswith("/agent-acceptance.json"):
                folder = name.rsplit("/", 1)[0]
                report = json.loads(archive.read(name))
                source = hashlib.sha256(archive.read(folder + "/base.apk")).hexdigest()
                candidate = hashlib.sha256(archive.read(folder + "/candidate.apk")).hexdigest()
                if source != report["input_sha256"] or not verified_for(report, candidate):
                    raise ValueError("样本的文件与独立验收不一致：" + folder)
                samples.append({"sample": folder, "candidate_sha256": candidate, "targets": report["targets"]})
        if not samples:
            raise ValueError("复核包缺少带完整验收报告的样本")
        counterexamples = []
        for name in names:
            if "反例样本" not in PurePosixPath(name).parts or not name.endswith("/agent-acceptance.json"):
                continue
            folder = name.rsplit("/", 1)[0]
            report = json.loads(archive.read(name))
            expected = json.loads(archive.read(folder + "/expected-outcome.json"))
            source = hashlib.sha256(archive.read(folder + "/base.apk")).hexdigest()
            candidate = hashlib.sha256(archive.read(folder + "/candidate.apk")).hexdigest()
            plan = expected["steps"]
            if (len(plan) != 2 or [s["action"] for s in plan] != ["expect", "absent"] or
                    plan[0]["value"] != plan[1]["value"] or
                    [s["status"] for s in plan] != ["passed", "failed"] or
                    any(s["match_count"] < 1 for s in plan) or
                    report["steps"] != [{k: s[k] for k in ("action", "value")} for s in plan]):
                raise ValueError("反例没有声明可复核的矛盾断言：" + folder)
            if (source != report["input_sha256"] or candidate != report["candidate_sha256"] or
                    report["status"] != "incomplete" or expected["exit_code"] != 1 or
                    len(report["rows"]) != len(report["targets"]) or not report["targets"]):
                raise ValueError("反例身份、目标或未通过状态无效：" + folder)
            targets = report["targets"]
            if any(type(api) is not int for api in targets) or len(targets) != len(set(targets)):
                raise ValueError("反例目标版本无效：" + folder)
            for api, row in zip(targets, report["rows"]):
                proof = row["candidate"]
                if row["api"] != api or proof["device"]["api"] != api:
                    raise ValueError("反例设备版本与复测目标不一致：" + folder)
                observed = [{k: step[k] for k in ("action", "value", "status", "match_count")}
                            for step in proof["steps"]]
                if (row["status"] != "failed" or proof["install"] != "passed" or proof["launch"] != "passed" or
                        proof["function"] != "failed" or proof["device"]["installed_sha256"] != candidate or
                        observed != expected["steps"]):
                    raise ValueError("反例没有对应到指定的行为失败：" + folder)
            counterexamples.append({"sample": folder, "candidate_sha256": candidate, "status": "incomplete"})
        return {"status": "archive-and-proofs-verified", "file_count": len(names),
                "local_links_checked": check_local_links(archive, set(names)),
                "samples": samples, "counterexamples": counterexamples,
                "package_status": status, "signed_release": signed_release,
                "release_regression": release_regression,
                "local_regression": local_regression,
                "local_review": local_review,
                "scope": "Files, checksums and stored proofs. Device replay is a separate operation."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package")
    args = parser.parse_args()
    print(json.dumps(verify(args.package), ensure_ascii=False, indent=2))
