"""Replay a downloaded agent acceptance report without calling a model."""
import argparse
import json
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_acceptance import parse_steps, verify, verified_for
from agent_device import AgentDevice
from agent_workspace import digest_file
from bench_device import Device, TOOLS, ready
from compatibility_matrix import normalize_targets
from reviver import INSTALL_FLOOR


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("base", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="New JSON file for the independent replay")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("复测报告需要一个新文件名")
    try:
        expected = json.loads(args.report.read_bytes())
        if expected["schema"] != 1:
            raise ValueError("验收报告版本不受支持")
        targets = normalize_targets(expected["targets"], INSTALL_FLOOR, 36)
        steps = parse_steps({"steps": expected["steps"]}) if expected["steps"] else []
        base_hash, candidate_hash = digest_file(args.base), digest_file(args.candidate)
        if base_hash != expected["input_sha256"] or candidate_hash != expected["candidate_sha256"]:
            raise ValueError("基准或候选 APK 与原验收报告的哈希不符")
    except (OSError, TypeError, KeyError, ValueError) as exc:
        parser.exit(2, f"无法启动复测：{exc}\n")
    device = Device()
    owner = "acceptance-replay-" + uuid.uuid4().hex

    def progress(message):
        print(message, file=sys.stderr, flush=True)

    def tester(api):
        return AgentDevice(device, owner, api, TOOLS, progress)

    try:
        report = verify(args.base, base_hash, args.candidate, candidate_hash, targets, steps, tester, ready, progress)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
    finally:
        device.stop()
    return 0 if (verified_for(report, candidate_hash) if steps else report["status"] == "launch-verified") else 1


if __name__ == "__main__":
    sys.exit(main())
