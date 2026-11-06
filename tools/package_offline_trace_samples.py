"""Package APK device checks reconstructed from recorded file edits."""
from argparse import ArgumentParser
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent_acceptance import verified_for
from tools.package_agent_review import FILE_CHECK_SCRIPT, digest, read
from tools.run_f01_p1 import plan
from tools.verify_review_package import OFFLINE_WINDOWS_FILES, verify


def build(source, results, exe, output, windows_evidence=None):
    if output.exists() or not results or any(not path.is_relative_to(source / "build") for path in results):
        raise ValueError("请选择新的输出包及候选检出目录内的复测结果")
    commit = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if subprocess.check_output(["git", "-C", str(source), "status", "--porcelain"]):
        raise ValueError("候选源码检出目录有未提交的改动")
    build_path = source / "build/local-exe-selftest-receipt.json"
    build_receipt = read(build_path)
    version, exe_hash = build_receipt["version"], digest(exe)
    if (build_receipt["source_commit"] != commit or build_receipt["self_test_exit"] != 0
            or build_receipt["exe_sha256"] != exe_hash):
        raise ValueError("离线重放的源码或 EXE 回执不一致")

    protocol = read(source / "tools/f01-p1c-cases.json")
    cases = {case["id"]: case for case in protocol["cases"]}
    samples = {}
    counterexamples = {}
    files = {
        "本地构建回执.json": build_path.read_bytes(),
        "校验文件.ps1": FILE_CHECK_SCRIPT.encode("utf-8-sig"),
        "复测样本.ps1": (ROOT / "tools/review_replay.ps1").read_text(encoding="utf-8-sig").encode("utf-8-sig"),
        "可复跑轨迹/重放工具.py": (ROOT / "tools/replay_agent_edit_trace.py").read_bytes(),
    }
    observations = []
    for index, directory in enumerate(results, 1):
        device_path = directory / "receipt.json"
        device = read(device_path)
        if (device["source_commit"] != commit or device["version"] != version or device["exe_sha256"] != exe_hash
                or device["api"] != 36 or device["abi"] != "x86_64" or device["network_model_calls"] != 0):
            raise ValueError("离线重放的设备、源码或 EXE 回执不一致")
        files[f"设备重放回执/{index}.json"] = device_path.read_bytes()
        observations.extend((directory, row) for row in device["rows"])

    for directory, row in observations:
        label = row["case"]
        if label not in cases or label in samples or label in counterexamples:
            raise ValueError("复测记录包含未知或重复用例")
        case = cases[label]
        negative = case["class"] == "negative"
        steps = plan(case["steps"])
        trial = source / "build" / f"offline-trace-{label}"
        base = source / case["apk"]
        candidate = trial / "candidate.apk"
        report_path = directory / f"{label}-replay.json"
        report, edit = read(report_path), read(trial / "receipt.json")
        trace = ROOT / "docs/validation/recorded-edit-fixtures" / f"{label}.json"
        recorded = read(trace)
        input_hash, candidate_hash = digest(base), digest(candidate)
        if (edit["implementation_commit"] != commit or edit["network_model_calls"] != 0
                or edit["input_sha256"] != input_hash or edit["candidate_sha256"] != candidate_hash
                or recorded["recorded_trace_sha256"] != edit["trace_sha256"]
                or recorded["source_sha256"] != input_hash or recorded["changes"] != edit["changes"]
                or row["candidate_sha256"] != candidate_hash or row["device_report_sha256"] != digest(report_path)
                or report["targets"] != [36] or report["input_sha256"] != input_hash or report["steps"] != steps
                or row["steps"] != len(steps) or row["status"] != report["status"]
                or "x86_64" not in report["rows"][0]["candidate"]["device"]["abis"]):
            raise ValueError(f"离线编辑与设备验收证据不一致 {label}")
        entry = {"candidate_sha256": candidate_hash, "steps": len(steps)}
        if negative:
            prefix = f"反例样本/{label}/"
            observed_steps = report["rows"][0]["candidate"]["steps"]
            expected = {"exit_code": 1, "steps": [{key: step[key] for key in ("action", "value", "status", "match_count")}
                                                  for step in observed_steps]}
            files[prefix + "expected-outcome.json"] = json.dumps(expected, ensure_ascii=False, indent=2).encode()
            counterexamples[label] = entry
        else:
            if not verified_for(report, candidate_hash):
                raise ValueError(f"正例未通过设备行为验收 {label}")
            prefix = f"可分发样本/{label}/"
            samples[label] = entry
        files.update({prefix + "base.apk": base.read_bytes(), prefix + "candidate.apk": candidate.read_bytes(),
                      prefix + "agent-acceptance.json": report_path.read_bytes(),
                      f"可复跑轨迹/{label}.json": trace.read_bytes(),
                      f"可复跑轨迹/{label}-编辑回执.json": (trial / "receipt.json").read_bytes()})
    if not samples:
        raise ValueError("没有可归档的设备通过样本")

    status = {"mode": "offline-trace-development-samples", "version": version, "source_commit": commit,
              "exe_sha256": exe_hash, "network_model_calls": 0, "samples": samples, "counterexamples": counterexamples}
    windows_note = ""
    if windows_evidence:
        for name, original in OFFLINE_WINDOWS_FILES.items():
            files[name] = (windows_evidence / original.format(version=version)).read_bytes()
        status["offline_windows"] = "fresh-install-inputs-and-desktop"
        windows_note = ("\nWindows 证据目录附新装系统回执、桌面截图与当时实际核验的原始 ZIP。"
                        "干净系统上的 Android 环境准备与设备步骤需另行验收。\n")
    negative_note = ""
    if counterexamples:
        negative_note = fr"""
矛盾断言反例必须选择 -Counterexample。保留 -CheckOnly 可只核对输入。去掉 -CheckOnly 后，预期候选安装和启动通过、第二个断言失败，脚本据此确认反例未获功能验收。

    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\复测样本.ps1 -LocalBuild -App "..\Lazarus-Agent.exe" -Sample {next(iter(counterexamples))} -Counterexample
"""
    files["复核状态.json"] = json.dumps(status, ensure_ascii=False, indent=2).encode()
    files["从这里开始.md"] = fr"""# v{version} 离线编辑设备复测样本

候选由已留档的文件工具操作重新回编、签名，并用同版 EXE 在 Android 16 x86_64 上独立验收。包内附原 APK、候选、固定步骤报告和可复跑轨迹。便携 EXE 单列，命令中的路径可换成实际位置。

    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\校验文件.ps1
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\复测样本.ps1 -LocalBuild -App "..\Lazarus-Agent.exe" -Sample {next(iter(samples))} -CheckOnly

准备好 Android 16 后，去掉 -CheckOnly 可重做设备验收。用 -Sample 指定包内其他用例。此次测试使用留档操作，没有调用模型服务。完整参赛回归见对应版本的工程评审包。

复跑编辑和签名需准备该版本的 APK 工具与干净 Git 检出。源码提交为 `{commit}`。将原 APK、对应的轨迹和检出目录传给包内 [重放工具](可复跑轨迹/重放工具.py)，输出目录位于该检出的 `build` 下。工具会比较编辑前后的文件哈希。
{negative_note}{windows_note}
""".encode()
    files["SHA256SUMS.txt"] = "".join(
        f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in sorted(files.items())
    ).encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent, prefix="offline-samples-") as temporary:
        staged = Path(temporary) / "samples.zip"
        with ZipFile(staged, "x", ZIP_DEFLATED) as archive:
            for name, data in sorted(files.items()):
                archive.writestr(ZipInfo(name), data, compress_type=ZIP_DEFLATED)
        checked = verify(staged)
        if len(checked["samples"]) != len(samples) or len(checked["counterexamples"]) != len(counterexamples):
            raise ValueError("归档复测样本数量不一致")
        staged.rename(output)
    return {**status, "archive_sha256": digest(output), "bytes": output.stat().st_size}


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    for field in ("source", "exe", "output"):
        parser.add_argument("--" + field, type=Path, required=True)
    parser.add_argument("--results", type=Path, action="append", required=True)
    parser.add_argument("--windows-evidence", type=Path)
    args = parser.parse_args()
    windows = args.windows_evidence.resolve() if args.windows_evidence else None
    receipt = build(args.source.resolve(), [path.resolve() for path in args.results],
                    args.exe.resolve(), args.output.resolve(), windows)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
