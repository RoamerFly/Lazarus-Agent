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
OFFLINE_WINDOWS_FILES = {
    "Windows复核/干净Windows复测回执.json": "干净 Windows 复测回执.json",
    "Windows复核/工作台.png": "干净 Windows 工作台.png",
    "Windows复核/当时复测样本.zip": "v{version}-离线编辑设备复测包.zip",
}
COLD_WINDOWS_FILE = "Windows复核/同版空目录准备回执.json"
COLD_COMPONENTS = {"platform-tools", "emulator", "build-tools", "system-image", "java"}
REVIEWER_GUIDE = "评审操作与答辩提纲.md"
SAMPLE_NOTICE = "评审样本使用说明.md"
DESKTOP_FILE = "Windows复核/干净Windows交互桌面回执.json"
HANDOFF_FILE = "专项复核/文件交接外部调用回执.json"
HANDOFF_PAIR = "专项复核/文件交接配对对照.zip"
RULE_FILE = "专项复核/无模型归档修复回执.json"
RULE_SAMPLE = "可分发样本/P02/"


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


def check_offline_windows(archive, checksums, status, samples):
    evidence = status.get("offline_windows")
    if evidence is None and not OFFLINE_WINDOWS_FILES.keys() & checksums.keys():
        return None
    if (status.get("mode") != "offline-trace-development-samples"
            or evidence != "fresh-install-inputs-and-desktop"
            or not OFFLINE_WINDOWS_FILES.keys() <= checksums.keys()):
        raise ValueError("离线复测包缺少完整的 Windows 证据")
    report = json.loads(archive.read("Windows复核/干净Windows复测回执.json"))
    guest, desktop = report["guest"], report["desktop"]
    setup, baseline = guest["setup"], guest["baseline"]
    build = json.loads(archive.read("本地构建回执.json"))
    for key in ("version", "source_commit", "exe_sha256"):
        if status[key] != setup[key] or status[key] != build[key]:
            raise ValueError("Windows 复测与本地 EXE 回执不一致")
    if (build["self_test_exit"] != 0 or setup["installed_exe_sha256"] != status["exe_sha256"]
            or any(setup[key] != 0 for key in ("install_exit", "installed_self_test_exit", "uninstall_exit"))
            or not setup["isolated_registration_removed"] or not setup["exported_output_preserved"]
            or guest["status"] != "passed" or report["blank_virtual_disk"] is not True
            or baseline["tool_commands"] or baseline["override_names"] or baseline["android_local_sdk"]
            or baseline["active_hardware_networks"] != 0 or guest["network_model_calls"] != 0
            or desktop["network_model_calls"] != 0 or desktop["android_16_ready"] or desktop["model_enabled"]
            or desktop["status"] != "workbench-started" or desktop["workbench_http"] != 200
            or desktop["portable_sha256"] != status["exe_sha256"]
            or desktop["renderer_processes"] < 1 or report["missing_webview2"]["status"] != "webview2-missing-prompt"
            or report["missing_webview2"]["renderer_processes"] != 0 or report["webview2"]["signature"] != "Valid"
            or guest["sample_zip_sha256"] != checksums["Windows复核/当时复测样本.zip"]
            or report["desktop_screenshot_sha256"] != checksums["Windows复核/工作台.png"]):
        raise ValueError("Windows 安装、桌面或历史样本包与回执不一致")
    with ZipFile(BytesIO(archive.read("Windows复核/当时复测样本.zip"))) as original:
        if len(original.namelist()) - 1 != report["verified_archive_files"] or "SHA256SUMS.txt" not in original.namelist():
            raise ValueError("干净 Windows 校验的文件数量与历史样本包不一致")
    by_case = {entry["sample"].removeprefix("可分发样本/"): entry for entry in samples}
    checked_cases = [row["case"] for row in guest["samples"]]
    if not checked_cases or len(set(checked_cases)) != len(checked_cases):
        raise ValueError("干净 Windows 复测样本与当前包内样本不一致")
    for row in guest["samples"]:
        case = by_case.get(row["case"])
        if not case or row["status"] != "inputs-verified" or case["candidate_sha256"] != row["candidate_sha256"]:
            raise ValueError("干净 Windows 复测样本与当前包内样本不一致")
        proof = json.loads(archive.read(case["sample"] + "/agent-acceptance.json"))
        if len(proof["steps"]) != row["steps"]:
            raise ValueError("干净 Windows 复测步骤数与当前设备报告不一致")
    return {"status": evidence, "os": baseline["product"], "checked_inputs": checked_cases}


def check_offline_cold_runtime(archive, checksums, status):
    scope = status.get("offline_cold_runtime")
    if scope is None and COLD_WINDOWS_FILE not in checksums:
        return None
    if scope != "same-exe-empty-storage-runtime" or COLD_WINDOWS_FILE not in checksums:
        raise ValueError("离线复测包缺少同版空目录准备回执")
    receipt = json.loads(archive.read(COLD_WINDOWS_FILE))
    build = json.loads(archive.read("本地构建回执.json"))
    started, prepared, install = (receipt[key] for key in ("preparation", "runtime", "install"))
    downloaded = {key.split(";")[0] for key, value in started["downloads"].items()
                  if value["current"] == value["total"] and value["total"] > 0}
    if (receipt["schema"] != 1 or receipt["status"] != scope or status["mode"] != "offline-trace-development-samples"
            or any(receipt[key] != status[key] or receipt[key] != build[key] or receipt[key] != install[key]
                   for key in ("version", "source_commit", "exe_sha256"))
            or build["self_test_exit"] != 0 or receipt["api"] != 36 or receipt["network_model_calls"] != 0
            or started["request_started"] is not True or started["status"] != "ready" or started["missing"]
            or started["ready"] is not True or started["reuse_without_download"] is not True
            or len(started["invalid_requests"]) != 7
            or {row["value"] for row in started["invalid_requests"]} !=
                {'false', '"false"', '"true"', '1', '[true]', '{"checked":true}', 'null'}
            or any(row["http"] != 400 for row in started["invalid_requests"])
            or set(started["before_missing"]) != COLD_COMPONENTS or downloaded != COLD_COMPONENTS
            or {name.split(";")[0] for name in started["verified"]} != COLD_COMPONENTS
            or set(prepared["components"]) != COLD_COMPONENTS
            or any(value is not True for value in prepared["components"].values())
            or prepared["version"] != status["version"] or prepared["api"] != 36 or prepared["terms_accepted"] is not True
            or prepared["status"] != "ready" or prepared["missing"] or prepared["reuse_without_download"] is not True
            or install["installed_exe_sha256"] != status["exe_sha256"]
            or any(install[key] != 0 for key in ("install_exit", "installed_self_test_exit", "uninstall_exit"))
            or install["isolated_registration_removed"] is not True or install["exported_output_preserved"] is not True
            or receipt["interactive_desktop_verified"] is not False or receipt["device_steps_on_guest"] != 0):
        raise ValueError("同版首次准备、组件复用或隔离安装证据不一致")
    return {"status": scope, "os": receipt["windows"], "api": 36,
            "components": sorted(COLD_COMPONENTS), "interactive_desktop_verified": False}


def check_offline_materials(archive, checksums, status, windows):
    scope = status.get("offline_materials")
    if scope is None and "遗留软件复活智能体离线演示.mp4" not in checksums:
        return None
    if scope != "same-build-offline-review" or windows is None:
        raise ValueError("同版材料缺少离线复核范围或 Windows 证据")
    version, commit = status["version"], status["source_commit"]
    receipt = json.loads(archive.read("同版材料校验回执.json"))
    source_name = f"F01-源代码-v{version}.zip"
    files = {source_name: "source_archive_sha256", "遗留软件复活智能体方案说明.pdf": "proposal_sha256",
             "遗留软件复活智能体离线演示.mp4": "video_sha256", "演示记录.json": "video_record_sha256",
             "演示设备画面.png": "device_screen_sha256", LOCAL_DEMO + "agent-acceptance.json": "acceptance_sha256"}
    if (receipt["app_version"] != version or receipt["source_commit"] != commit
            or receipt["exe_sha256"] != status["exe_sha256"]
            or any(checksums.get(name) != receipt[key] for name, key in files.items())):
        raise ValueError("同版方案、视频、设备证据或源码与回执不一致")
    guide = status.get("offline_review_guide")
    guide_files = {REVIEWER_GUIDE, SAMPLE_NOTICE}
    if guide is not None or guide_files & checksums.keys() or "reviewer_docs_sha256" in receipt:
        recorded = receipt.get("reviewer_docs_sha256", {})
        if (guide != "current-version-guide" or set(recorded) != guide_files
                or any(checksums.get(name) != recorded[name] for name in guide_files)):
            raise ValueError("同版评审提纲或样本使用说明缺少完整校验")
    with ZipFile(BytesIO(archive.read(source_name))) as code:
        if (code.comment.decode("ascii") != commit
                or f'APP_VERSION = "{version}"'.encode("ascii") not in code.read("app_version.py").splitlines()):
            raise ValueError("离线方案源码与构建提交不一致")
        if guide and code.read(f"fixtures/{SAMPLE_NOTICE}") != archive.read(SAMPLE_NOTICE):
            raise ValueError("样本使用说明与同版源码不一致")
    record = json.loads(archive.read("演示记录.json"))
    capture = record["capture"]
    screen = capture["device_screen"]
    report = json.loads(archive.read(LOCAL_DEMO + "agent-acceptance.json"))
    prior = json.loads(archive.read("可分发样本/P01/agent-acceptance.json"))
    candidate = checksums.get(LOCAL_DEMO + "candidate.apk")
    if (record["app_version"] != version or record["video_sha256"] != receipt["video_sha256"]
            or record["duration_planned_seconds"] != receipt["video_seconds"]
            or capture["app_version"] != version or capture["source_commit"] != commit
            or capture.get("exe_sha256", None if status.get("offline_cold_runtime") else status["exe_sha256"])
                != status["exe_sha256"]
            or capture["mode"] != "recorded-tools" or capture["network_model_calls"] != 0
            or capture["trace_sha256"] != checksums.get("可复跑轨迹/P01.json")
            or capture["input_sha256"] != report["input_sha256"]
            or prior["input_sha256"] != report["input_sha256"] or prior["steps"] != report["steps"]
            or capture["candidate_sha256"] != candidate or screen["apk_sha256"] != candidate
            or screen["sha256"] != receipt["device_screen_sha256"]
            or screen["api"] != 36 or report["targets"] != [36]
            or report["rows"][0]["baseline"]["install"] != "failed" or not verified_for(report, candidate)):
        raise ValueError("离线演示的轨迹、原包、录制或 Android 验收证据不一致")
    result = {"status": scope, "version": version, "demo_steps": len(report["steps"]), "network_model_calls": 0}
    if guide:
        result["reviewer_guide"] = guide
    return result


def check_offline_addendum(archive, checksums, status, materials):
    files = {DESKTOP_FILE, HANDOFF_FILE, HANDOFF_PAIR, RULE_FILE}
    scope = status.get("offline_review_addendum")
    if scope is None and not files.intersection(checksums):
        return None
    if scope != "same-build-specialized-proofs" or not materials or not files <= checksums.keys():
        raise ValueError("同版评审包缺少完整专项复核")

    desktop_receipt = json.loads(archive.read(DESKTOP_FILE))
    rule = json.loads(archive.read(RULE_FILE))
    version, commit, exe = (status[key] for key in ("version", "source_commit", "exe_sha256"))
    if any((receipt["app_version"], receipt["source_commit"], receipt["exe_sha256"]) != (version, commit, exe)
           for receipt in (desktop_receipt, rule)):
        raise ValueError("专项复核与同版构建身份不一致")

    guest, desktop = (desktop_receipt[key] for key in ("guest", "desktop"))
    setup = guest["setup"]
    if (desktop_receipt["blank_virtual_disk"] is not True
            or desktop_receipt["android_device_tested_in_this_guest"] is not False
            or guest["status"] != "passed" or desktop["status"] != "workbench-started"
            or desktop["session_id"] < 1 or desktop["renderer_processes"] < 1 or not desktop["native_window_handle"]
            or desktop["workbench_http"] != 200 or desktop["webview2_publisher"] != "Valid"
            or guest["network_model_calls"] != 0 or desktop["network_model_calls"] != 0
            or any(guest["baseline"][key] for key in
                   ("tool_commands", "override_names", "android_local_sdk", "active_hardware_networks"))
            or setup["source_commit"] != commit or setup["version"] != version
            or not (setup["exe_sha256"] == setup["installed_exe_sha256"] == desktop["portable_sha256"] == exe)
            or any(setup[key] != 0 for key in ("install_exit", "installed_self_test_exit", "uninstall_exit"))
            or not setup["exported_output_preserved"] or not setup["isolated_registration_removed"]
            or [row["case"] for row in guest["samples"]] != ["P03", "P05"]):
        raise ValueError("干净 Windows 交互复核证据不一致")
    for row in guest["samples"]:
        prefix = f"可分发样本/{row['case']}/"
        report = json.loads(archive.read(prefix + "agent-acceptance.json"))
        if (row["status"] != "inputs-verified" or row["steps"] != len(report["steps"])
                or row["candidate_sha256"] != checksums[prefix + "candidate.apk"]):
            raise ValueError("干净 Windows 复核样本与同版归档不一致")

    proof = json.loads(archive.read(RULE_SAMPLE + "agent-acceptance.json"))
    candidate = checksums[RULE_SAMPLE + "candidate.apk"]
    steps = rule["candidate"]["steps"]
    libraries = rule["diff"]["manifest"]["libraries"]
    if (rule["case"] != "P02" or rule["target_api"] != 36 or proof["targets"] != [36]
            or rule["diagnosis_source"] != "rules" or rule["model_status"] != "not-configured"
            or rule["real_model_calls"] != 0 or rule["original"]["click_result"] != "failed"
            or rule["original"]["missing_class"].replace("/", ".") != "org.apache.http.ProtocolVersion"
            or rule["original"]["sha256"] != checksums[RULE_SAMPLE + "base.apk"]
            or rule["proof_sha256"] != checksums[RULE_SAMPLE + "agent-acceptance.json"]
            or not (rule["candidate"]["sha256"] == rule["candidate"]["installed_sha256"] == candidate)
            or [step["action"] for step in steps] != ["expect", "tap", "expect", "cold_start", "tap", "expect"]
            or any(step["status"] != "passed" for step in steps)
            or libraries != {"removed": [], "added": [{"type": "uses-library", "name": "org.apache.http.legacy"}]}
            or not verified_for(proof, candidate)):
        raise ValueError("无模型修复回执与独立设备复测不一致")

    handoff = json.loads(archive.read(HANDOFF_FILE))
    run = handoff["result"]
    probe = run["uri_before_and_after"]
    with ZipFile(BytesIO(archive.read(HANDOFF_PAIR))) as paired:
        paired_apk = paired.read("prior-agent-candidate.apk")
        paired_proof = paired.read("prior-agent-candidate-acceptance.json")
    if (handoff["app_version"] != version or handoff["paired_acceptance_exe_sha256"] != exe
            or run["status"] != "verified" or run["source_commit"] != commit or run["network_model_calls"] != 0
            or run["acceptance_sha256"] != hashlib.sha256(paired_proof).hexdigest()
            or not (run["candidate_sha256"] == probe["candidate_and_installed_sha256"]
                    == hashlib.sha256(paired_apk).hexdigest())
            or not verified_for(json.loads(paired_proof), run["candidate_sha256"])
            or probe["api"] != 36 or len(set(probe["uids"].values())) != 2
            or {probe["before_share"]["outcome"], probe["outcome"], run["forged_bytes_after"]["outcome"]}
                != {"target-rejected"}):
        raise ValueError("外部调用结果与文件交接候选的设备证据不一致")
    return {"status": scope, "windows_desktop": desktop["status"], "local_rule_steps": len(steps),
            "external_target_rejections": 3}


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
        offline_windows = check_offline_windows(archive, checksums, status, samples)
        offline_cold_runtime = check_offline_cold_runtime(archive, checksums, status)
        offline_materials = check_offline_materials(archive, checksums, status, offline_windows or offline_cold_runtime)
        addendum = check_offline_addendum(archive, checksums, status, offline_materials)
        return {"status": "archive-and-proofs-verified", "file_count": len(names),
                "local_links_checked": check_local_links(archive, set(names)),
                "samples": samples, "counterexamples": counterexamples,
                "package_status": status, "signed_release": signed_release,
                "release_regression": release_regression,
                "local_regression": local_regression,
                "local_review": local_review, "offline_windows": offline_windows,
                "offline_cold_runtime": offline_cold_runtime, "offline_materials": offline_materials,
                "offline_addendum": addendum,
                "scope": "Files, checksums and stored proofs. Device replay is a separate operation."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package")
    args = parser.parse_args()
    print(json.dumps(verify(args.package), ensure_ascii=False, indent=2))
