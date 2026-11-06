"""Package and verify the same-build Windows and frozen Android evidence."""
from argparse import ArgumentParser
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent_acceptance import verified_for
from tools.run_f01_p1 import plan as device_plan, trial_schedule
from tools.replay_f01_recorded_cohort import (
    HISTORICAL, RECORDED_INPUT, RECORDED_INPUT_SHA256, score, summary,
)

PREFIX = "冻结评测/"
GUIDE = PREFIX + "复核与复跑.md"
RUNTIME = "Windows复核/同版首次下载回执.json"
PLAN = PREFIX + "plan.json"
AUDIT = PREFIX + "audit.json"
REPLAY = PREFIX + "tools/replay_f01_recorded_cohort.py"
HISTORY = PREFIX + "docs/validation/f01-p1c/results.json"
TRACE = PREFIX + "docs/validation/f01-p1c/replay-input.json"
COMPONENTS = {"platform-tools", "emulator", "build-tools", "system-image", "java"}
CONSENT_VALUES = {'false', '"false"', '"true"', '1', '[true]', '{"checked":true}', 'null'}
SCOPE = "offline-replay-of-historical-tool-decisions"


def verify_runtime(receipt, build):
    first = receipt["first_use"]
    invalid = first["invalid_requests"]
    components = first["components"]
    if (receipt["schema"] != 1 or receipt["status"] != "first-download-and-reuse-verified"
            or any(receipt[key] != build[key] for key in ("version", "source_commit", "exe_sha256"))
            or receipt["target_api"] != 36 or receipt["network_model_calls"] != 0
            or set(first["before_missing"]) != COMPONENTS or len(invalid) != len(CONSENT_VALUES)
            or {row["value"] for row in invalid} != CONSENT_VALUES or any(row["http"] != 400 for row in invalid)
            or first["invalid_requests_started_preparation"] is not False
            or first["authorized_download_started"] is not True or first["reuse_without_download"] is not True
            or len(components) != len(COMPONENTS) or {row["id"].split(";")[0] for row in components} != COMPONENTS
            or any(type(row["downloaded_bytes"]) is not int or row["downloaded_bytes"] <= 0
                   or row["publisher_hash_checked"] is not True for row in components)
            or sum(row["downloaded_bytes"] for row in components) != first["downloaded_bytes"]
            or first["elapsed_seconds"] <= 0 or receipt["probe"]["guest_device_steps_run"] is not False):
        raise ValueError("同版 Windows 首次下载或复用证据不一致")
    return {"components": len(components), "downloaded_bytes": first["downloaded_bytes"],
            "elapsed_seconds": first["elapsed_seconds"], "guest_device_steps_run": False}


def verify_device_report(report, case, row, api):
    candidate = row["candidate_sha256"]
    if (report["input_sha256"] != row["source_sha256"] or report["candidate_sha256"] != candidate
            or report["targets"] != [api] or report["steps"] != device_plan(case["steps"]) or len(report["rows"]) != 1
            or report["rows"][0]["api"] != api):
        raise ValueError(f"冻结设备报告与候选或步骤不一致 {row['trial']}")
    observed = report["rows"][0]
    for side, expected in (("baseline", row["source_sha256"]), ("candidate", candidate)):
        stage = observed[side]
        if stage["install"] == "passed":
            device = stage["device"]
            if device["installed_sha256"] != expected or device["api"] != api or "x86_64" not in device["abis"]:
                raise ValueError(f"冻结设备安装回读不一致 {row['trial']}")
    if {key: observed["candidate"][key] for key in ("build", "install", "launch", "function")} != row["candidate_status"]:
        raise ValueError(f"冻结候选阶段不一致 {row['trial']}")
    return {"comparison": observed["comparison"], "behavior_verified": verified_for(report, candidate),
            "result": {"outcome": "candidate", "sha256": candidate}}


def verify_early_rejection(result, row, case, api):
    baseline = result["baseline"]
    device = baseline["device"]
    if (result["source_sha256"] != row["source_sha256"] or result["case"] != row["case"]
            or result["repeat"] != row["repeat"] or result["mode"] != row["mode"] or result["api"] != api
            or result["model_usage"] or result.get("error") != row["tool_error"]
            or row["candidate_sha256"] or row["candidate_status"]
            or baseline["sha256"] != row["source_sha256"] or device["api"] != api or "x86_64" not in device["abis"]):
        raise ValueError(f"冻结反例结果与计划不一致 {row['trial']}")
    if case["id"] == "N02":
        steps = baseline["steps"]
        expected = device_plan(case["steps"])
        if (baseline["install"] != "passed" or baseline["launch"] != "passed"
                or device["installed_sha256"] != row["source_sha256"] or len(steps) != len(expected)
                or any(step["status"] != "failed" or step["match_count"] != 0
                       or any(step[key] != value for key, value in assertion.items())
                       for step, assertion in zip(steps, expected))):
            raise ValueError(f"冻结反例缺少数据断言失败证据 {row['trial']}")


def verify_extension(archive, checksums, status):
    declared = status.get("same_build_evidence")
    present = {name for name in checksums if name == RUNTIME or name.startswith(PREFIX)}
    if declared is None and not present:
        return None
    required = {AUDIT, GUIDE, HISTORY, PLAN, REPLAY, RUNTIME, TRACE}
    if declared != "windows-and-recorded-cohort" or not required <= present:
        raise ValueError("缺少完整的同版补充复核材料")
    def load(name):
        return json.loads(archive.read(name))

    build = load("本地构建回执.json")
    runtime = verify_runtime(load(RUNTIME), build)
    plan, audit, history, traces = (load(name) for name in (PLAN, AUDIT, HISTORY, TRACE))
    if (any(build[key] != status[key] for key in ("version", "source_commit", "exe_sha256"))
            or build["self_test_exit"] != 0 or plan["schema"] != 1 or audit["schema"] != 1
            or any(item["scope"] != SCOPE or item["api"] != 36 or item["build"] != build
                   or item["network_model_calls"] != 0 or item["historical_results_sha256"] != checksums[HISTORY]
                   for item in (plan, audit))
            or checksums[TRACE] != RECORDED_INPUT_SHA256 or traces["historical_results_sha256"] != checksums[HISTORY]
            or audit["plan_sha256"] != checksums[PLAN]):
        raise ValueError("冻结批次与同版构建、计划或留档输入不一致")
    with ZipFile(BytesIO(archive.read(f"F01-源代码-v{build['version']}.zip"))) as source:
        raw = source.read("tools/f01-p1c-cases.json")
        protocol = json.loads(raw)
        source_hashes = {case["id"]: sha256(source.read(case["apk"])).hexdigest() for case in protocol["cases"]}
    if (any(item["protocol_sha256"] != sha256(raw).hexdigest() for item in (plan, audit, history, traces))
            or protocol["target"] != 36 or history["source_apk_sha256"] != source_hashes):
        raise ValueError("冻结协议或原 APK 与同版源码不一致")

    cases = {case["id"]: case for case in protocol["cases"]}
    scheduled = {f"{case['id']}-{mode}-{repeat}": {"case": case["id"], "class": case["class"], "mode": mode,
                                                  "repeat": repeat, "source_sha256": source_hashes[case["id"]]}
                 for case, mode, repeat in trial_schedule(protocol)}
    historical = {row["trial"]: row["state"] for row in history["rows"]}
    rows = audit["rows"]
    if (len(scheduled) != history["planned_trials"] or len(rows) != len(scheduled)
            or set(plan["trials"]) != scheduled.keys() or historical.keys() != scheduled.keys()
            or [row["trial"] for row in rows] != list(scheduled)):
        raise ValueError("冻结批次缺少计划中的唯一轮次")
    proofs = 0
    for row in rows:
        label = row["trial"]
        binding = {**scheduled[label], "historical_state": historical[label]}
        if row["mode"] in {"agent", "one-build"}:
            binding["historical_trace_sha256"] = traces["traces"][label]["trace_sha256"]
        if (binding != plan["trials"][label] or any(row[key] != value for key, value in binding.items())
                or row["terminal"] is not True or row["network_model_calls"] != 0):
            raise ValueError(f"冻结轮次身份或终态不一致 {label}")
        case = cases[row["case"]]
        folder = f"{PREFIX}设备证据/{label}/"
        proof = {}
        result = {"error": row["tool_error"]}
        if row["acceptance_sha256"]:
            name = folder + "acceptance.json"
            if checksums.get(name) != row["acceptance_sha256"]:
                raise ValueError(f"冻结轮次缺少对应设备报告 {label}")
            required.add(name)
            proof = load(name)
            result.update(verify_device_report(proof, case, row, plan["api"]))
            proofs += 1
        elif row["case"] in {"N01", "N02"}:
            name = folder + "result.json"
            if checksums.get(name) != row["result_sha256"]:
                raise ValueError(f"冻结反例缺少原始结果 {label}")
            required.add(name)
            result = load(name)
            verify_early_rejection(result, row, case, plan["api"])
        elif row["candidate_sha256"] or row["candidate_status"]:
            raise ValueError(f"冻结候选缺少设备报告 {label}")
        if score(case, result, proof) != row["state"]:
            raise ValueError(f"冻结轮次计分与设备证据不一致 {label}")
    counts = summary(protocol, rows)
    if present != required or counts != audit["counts"]:
        raise ValueError("冻结批次证据清单或计分不一致")
    return {"windows": runtime, "cohort": counts, "device_reports": proofs, "network_model_calls": 0}


def extend(base, output, runtime, audit, batch):
    from tools.verify_review_package import verify

    if output.exists():
        raise ValueError("请选择新的补充评审包路径")
    verified = verify(base)
    if not verified["offline_materials"] or verified["same_build_evidence"]:
        raise ValueError("需要已核验且尚未补充的同版评审包")
    with ZipFile(base) as archive:
        files = {name: archive.read(name) for name in archive.namelist() if name != "SHA256SUMS.txt"}
    status = verified["package_status"]
    status["same_build_evidence"] = "windows-and-recorded-cohort"
    files["复核状态.json"] = json.dumps(status, ensure_ascii=False, indent=2).encode()
    files.update({RUNTIME: runtime.read_bytes(), AUDIT: audit.read_bytes(), PLAN: (batch / "plan.json").read_bytes(),
                  REPLAY: (ROOT / "tools/replay_f01_recorded_cohort.py").read_bytes(),
                  HISTORY: HISTORICAL.read_bytes(), TRACE: RECORDED_INPUT.read_bytes()})
    audited = json.loads(files[AUDIT])
    for row in audited["rows"]:
        label = row["trial"]
        name = "acceptance.json" if row["acceptance_sha256"] else "result.json" if row["case"] in {"N01", "N02"} else None
        if name:
            files[f"{PREFIX}设备证据/{label}/{name}"] = (batch / label / name).read_bytes()
    files[GUIDE] = guide(status, audited["counts"]).encode()
    intro = files["从这里开始.md"].decode("utf-8")
    intro = intro.replace("空白系统首次准备见 v0.6.28。", "空白 Windows 系统安装见 v0.6.28。")
    intro += f"\n[同版首次下载回执]({RUNTIME}) · [42 轮冻结评测和复跑入口]({GUIDE})\n"
    files["从这里开始.md"] = intro.encode()
    files["SHA256SUMS.txt"] = "".join(f"{sha256(data).hexdigest()}  {name}\n"
                                     for name, data in sorted(files.items())).encode()
    with TemporaryDirectory(dir=output.parent, prefix="same-build-evidence-") as directory:
        staged = Path(directory) / "review.zip"
        with ZipFile(staged, "x", ZIP_DEFLATED) as archive:
            for name, data in sorted(files.items()):
                archive.writestr(ZipInfo(name), data, compress_type=ZIP_DEFLATED)
        checked = verify(staged)
        if not checked["same_build_evidence"] or staged.stat().st_size > 100 * 1024 * 1024:
            raise ValueError("补充评审包缺少证据或超过大小限制")
        staged.rename(output)
    return {"archive": output.name, "archive_sha256": sha256(output.read_bytes()).hexdigest(),
            "bytes": output.stat().st_size, "registered_files": checked["file_count"] - 1, **checked["same_build_evidence"]}


def guide(status, counts):
    return f"""# v{status['version']} 同版冻结评测复核

[Windows 首次下载和复用](../{RUNTIME}) · [42 轮结果](audit.json) · [冻结计划](plan.json)

Windows 回执覆盖专用来宾从空应用数据目录下载五项组件及复用。Android 设备行为在宿主机专用 Android 16 x86_64 模拟器验证。空白 Windows 系统安装见此前版本的单独回执。

复跑使用历史模型留档的工具参数，真实模型调用为零。正例功能通过 {counts['positive_passed']}/{counts['positive_planned']}，反例拒绝 {counts['negative_rejected']}/{counts['negative_planned']}，错误验收 {counts['negative_false_acceptances']}。设备结果未观测的 {counts['unobserved']} 轮保留在原计划分母。一次构建、无改动回编、本地规则和 Agent 各自计分。历史模型的独立决策记录另列。

逐轮设备报告保存在「设备证据」目录。审核回执分别登记原包、候选、设备报告和操作轨迹哈希。该目录用于审阅证据。可安装样本位于评审包根目录的「可分发样本」。

只复跑演示或可分发样本时，返回[评审包入口](../从这里开始.md)并使用其中的 PowerShell 命令。

完整复跑需要 Python、Git、已准备的 Android 16 专用模拟器和源码检出 `{status['source_commit']}`。可从 RoamerFly/Lazarus-Agent 仓库检出该提交。保留本目录结构并将交付 EXE 与[本地构建回执](../本地构建回执.json)分别放在源码检出的 `dist/Lazarus-Agent.exe` 和 `build/local-exe-selftest-receipt.json`。在本目录运行下列命令，将两处路径替换为实际源码目录。

    python -X utf8 tools/replay_f01_recorded_cohort.py --checkout "源码目录" --output "源码目录/build/f01-recorded-cohort" --check-only

预检核对 42 轮计划、原包、工具参数、源码提交和 EXE 哈希。去掉 `--check-only` 后从原 APK 开始重新构建并执行设备步骤。
"""


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    for field in ("base", "output", "runtime", "audit", "batch"):
        parser.add_argument("--" + field, type=Path, required=True)
    print(json.dumps(extend(**vars(parser.parse_args())), ensure_ascii=False, indent=2))
