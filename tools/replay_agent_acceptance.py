"""Replay a downloaded agent acceptance report without calling a model."""
import argparse
from contextlib import nullcontext
import json
from pathlib import Path
import re
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_acceptance import parse_steps, verify, verified_for
from agent_device import AgentDevice
from agent_workspace import digest_file
import bench_device
from compatibility_matrix import normalize_targets
from diagnose import inspect_package
from reviver import INSTALL_FLOOR


def read_inputs(report, base, candidate):
    with report.open("rb") as stream:
        encoded = stream.read(8 * 1024 * 1024 + 1)
    if len(encoded) > 8 * 1024 * 1024:
        raise ValueError("验收报告超过 8 MB")
    expected = json.loads(encoded)
    if not isinstance(expected, dict) or type(expected.get("schema")) is not int or expected["schema"] != 1:
        raise ValueError("验收报告版本不受支持")
    if not isinstance(expected.get("targets"), list):
        raise ValueError("验收报告缺少目标 Android 版本")
    targets = normalize_targets(expected["targets"], INSTALL_FLOOR, 36)
    if not isinstance(expected.get("steps"), list):
        raise ValueError("验收报告缺少步骤列表")
    steps = parse_steps({"steps": expected["steps"]}) if expected["steps"] else []
    for field, apk in (("input_sha256", base), ("candidate_sha256", candidate)):
        if not isinstance(expected.get(field), str) or not re.fullmatch("[a-f0-9]{64}", expected[field]):
            raise ValueError("验收报告缺少有效的 APK 哈希")
        if apk.suffix.lower() != ".apk" or not apk.is_file():
            raise ValueError("请选择完整的单个 APK 文件")
        if digest_file(apk) != expected[field]:
            raise ValueError("基准或候选 APK 与原验收报告的哈希不符")
    return targets, steps, expected["input_sha256"], expected["candidate_sha256"]


def replay(args, progress):
    targets, steps, base_hash, candidate_hash = read_inputs(args.report, args.base, args.candidate)
    if args.check_only:
        return {"status": "inputs-verified", "targets": targets, "steps": steps,
                "input_sha256": base_hash, "candidate_sha256": candidate_hash}, True
    if not bench_device.ADB.is_file() or not (bench_device.TOOLS / "aapt.exe").is_file():
        raise ValueError("请先在工作台的运行环境中准备 Android 验证组件")
    base = inspect_package(args.base, bench_device.TOOLS)
    candidate = inspect_package(args.candidate, bench_device.TOOLS)
    if base["package"] != candidate["package"]:
        raise ValueError("基准和候选 APK 的包名不同")
    device = bench_device.Device(allow_existing=False)
    owner = "acceptance-replay-" + uuid.uuid4().hex

    def tester(api):
        return AgentDevice(device, owner, api, bench_device.TOOLS, progress)

    try:
        report = verify(args.base, base_hash, args.candidate, candidate_hash, targets, steps,
                        tester, bench_device.ready, progress)
    finally:
        device.stop()
    passed = verified_for(report, candidate_hash) if steps else report["status"] == "launch-verified"
    return report, passed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("base", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="New JSON file for the independent replay")
    parser.add_argument("--log", type=Path, help="New progress log for the Windows desktop executable")
    parser.add_argument("--check-only", action="store_true", help="Check input hashes and steps without a device")
    args = parser.parse_args(argv)
    if args.output.exists() or (args.log and args.log.exists()):
        parser.error("请为复测报告和日志使用新文件名")
    if args.log and args.output.resolve() == args.log.resolve():
        parser.error("复测报告与日志应使用不同文件")
    try:
        with (args.log.open("x", encoding="utf-8") if args.log else nullcontext(sys.stderr)) as log:
            def progress(message):
                if log is not None:
                    print(message, file=log, flush=True)

            try:
                report, passed = replay(args, progress)
                with args.output.open("x", encoding="utf-8") as stream:
                    json.dump(report, stream, ensure_ascii=False, indent=2)
                progress(report["status"])
            except (OSError, TypeError, KeyError, ValueError, RuntimeError) as exc:
                progress(f"无法完成复测：{exc}")
                return 2
    except OSError as exc:
        parser.exit(2, f"无法创建日志：{exc}\n")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
