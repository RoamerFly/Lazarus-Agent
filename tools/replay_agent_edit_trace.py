"""Replay recorded file edits through the Agent and build a signed APK without a model."""
from argparse import ArgumentParser
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch


FILE_TOOLS = {
    "list_files", "search_text", "read_text", "read_method", "replace_text",
    "replace_lines", "create_text_file", "restore_file",
}
SKIPPED_TOOLS = {"test_original", "test_apk", "device_action", "rehearse_acceptance", "build_apk", "finish"}


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def replay(checkout, apk, trace_path, api, output, *, allow_unfinished=False):
    sys.path.insert(0, str(checkout))
    from bench_device import TOOLS
    import feature_agent

    commit = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
    if subprocess.check_output(["git", "-C", str(checkout), "status", "--porcelain"]):
        raise ValueError("请使用干净的实现检出目录")
    if output.exists() or not output.is_relative_to(checkout / "build"):
        raise ValueError("请选择实现目录 build 下的新输出目录")
    trace = json.loads(trace_path.read_text(encoding="utf-8-sig"))
    if sha256(apk) != trace["source_sha256"]:
        raise ValueError("轨迹应对应同一输入 APK")
    outcome = trace.get("outcome")
    unfinished = "outcome" in trace and outcome is None and bool(trace.get("changes"))
    if (outcome or {}).get("outcome") != "candidate" and not (allow_unfinished and unfinished):
        raise ValueError("请选择已生成候选的轨迹，或显式指定重放未完成编辑")
    calls = []
    for row in trace["tools"]:
        if not row["ok"]:
            continue
        name, args = row["tool"], row["arguments"]
        if name in FILE_TOOLS or (name == "inspect_manifest" and args == {"artifact": "original"}):
            calls.append((name, args))
        elif name not in SKIPPED_TOOLS and name != "inspect_manifest":
            raise ValueError(f"当前离线重放未覆盖有状态工具 {name}")
    calls.extend([
        ("build_apk", {}),
        ("finish", {"outcome": "candidate", "summary": "离线重放已完成回编和签名，设备状态未验证。"}),
    ])
    if len(calls) > feature_agent.MAX_ROUNDS:
        raise ValueError("离线轨迹超过当前 Agent 的轮次上限")
    index = 0

    def next_recorded_call(_url, payload, _key, *, timeout):
        nonlocal index
        if index:
            last = next(json.loads(message["content"]) for message in reversed(payload["messages"])
                        if message["role"] == "tool")
            if not last["ok"]:
                raise ValueError(f"离线工具重放失败 {calls[index - 1][0]} {last['error']}")
        name, args = calls[index]
        index += 1
        return {"tool_calls": [{"id": f"offline-{index}",
                               "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]}

    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent, prefix="trace-replay-") as temporary:
        stage = Path(temporary) / "result"
        candidate = stage / "candidate.apk"
        with patch.object(feature_agent, "_request", side_effect=next_recorded_call):
            result = feature_agent.run(
                apk, candidate, TOOLS, api, trace["source_sha256"], "复现记录中的 APK 文件操作",
                {"url": "http://127.0.0.1", "model": "recorded-tools", "key": ""}, lambda _: None)
        if (index != len(calls) or result["outcome"] != "candidate" or result["charged_build_attempts"] != 1
                or result["changes"] != trace["changes"] or result["sha256"] != sha256(candidate)):
            raise ValueError("重放的编辑差异或 APK 构建结果不一致")
        receipt = {"implementation_commit": commit, "input_sha256": trace["source_sha256"],
                   "trace_sha256": sha256(trace_path), "candidate_sha256": result["sha256"], "api": api,
                   "replayed_calls": len(calls), "network_model_calls": 0,
                   "verified": "recorded-changes-rebuilt-and-signed", "device": "not-run",
                   "changes": result["changes"]}
        if unfinished:
            receipt["input_trace_state"] = "unfinished-edits"
        (stage / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        stage.rename(output)
    return receipt


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("checkout", type=Path)
    parser.add_argument("apk", type=Path)
    parser.add_argument("trace", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--api", type=int, default=36)
    parser.add_argument("--allow-unfinished", action="store_true",
                        help="Build recorded edits from a trace that stopped before producing a candidate")
    args = parser.parse_args()
    print(json.dumps(replay(args.checkout.resolve(), args.apk.resolve(), args.trace.resolve(),
                            args.api, args.output.resolve(), allow_unfinished=args.allow_unfinished),
                     ensure_ascii=False, indent=2))
