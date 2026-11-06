"""Replay the fixed historical F01 tool decisions through a current checkout."""
from argparse import ArgumentParser
from collections import Counter
from hashlib import file_digest
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
HISTORICAL = ROOT / "docs/validation/f01-p1c/results.json"


def read(path):
    return json.loads(path.read_bytes())


def digest(path):
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def tool_sequence(rows):
    return [(row["tool"], row["arguments"]) for row in rows]


def score(case, result, proof):
    if result.get("error"):
        return "unobserved"
    if case["class"] == "positive":
        if not result.get("behavior_verified"):
            return "failed"
        return ("passed" if result.get("comparison") in {"baseline-check-failed", "baseline-install-failed"}
                else "unattributed")
    if result.get("behavior_verified"):
        return "false-acceptance"
    outcome = result.get("result")
    if not outcome:
        return "unobserved"
    if case["id"] == "N03":
        rows = proof.get("rows", [])
        if len(rows) != 1:
            return "unobserved"
        observed = rows[0]["candidate"]
        steps = observed["steps"]
        if (observed["install"] == observed["launch"] == "passed" and observed["function"] == "failed"
                and [step["status"] for step in steps] == ["passed", "failed"]
                and [step["action"] for step in steps] == ["expect", "absent"]
                and all(step["match_count"] > 0 for step in steps)):
            return "rejected"
        return "unobserved"
    if outcome["outcome"] not in {"analysis", "blocked", "needs_input"} or outcome.get("sha256"):
        return "unobserved"
    baseline = result.get("baseline", {})
    if case["id"] == "N01":
        installation = (baseline.get("device") or {}).get("install", {})
        return ("rejected" if baseline.get("install") == "failed"
                and "NO_MATCHING_ABIS" in installation.get("output", "") else "unobserved")
    return "rejected" if baseline.get("function") == "failed" else "unobserved"


def summary(protocol, rows):
    positives = [row for row in rows if row["mode"] == "agent" and row["class"] == "positive"]
    negatives = [row for row in rows if row["mode"] == "agent" and row["class"] == "negative"]
    return {
        "terminal": sum(row["terminal"] for row in rows),
        "unobserved": sum(row["state"] == "unobserved" for row in rows),
        "positive_passed": sum(row["state"] == "passed" for row in positives),
        "positive_planned": len(positives),
        "primary_passed": sum(row["state"] == "passed" for row in positives if row["case"] == protocol["primary"]),
        "historical_holdout_passed": sum(row["state"] == "passed" for row in positives if row["case"] in protocol["held_out"]),
        "negative_rejected": sum(row["state"] == "rejected" for row in negatives),
        "negative_false_acceptances": sum(row["state"] == "false-acceptance" for row in negatives),
        "negative_planned": len(negatives),
        "controls": {mode: dict(Counter(row["state"] for row in rows if row["mode"] == mode))
                     for mode in ("roundtrip", "rules", "one-build")},
    }


def replay(checkout, historical_trials, output):
    if not output.is_relative_to(checkout / "build"):
        raise ValueError("复跑输出必须位于候选检出目录的 build 下")
    commit = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
    if subprocess.check_output(["git", "-C", str(checkout), "status", "--porcelain"]):
        raise ValueError("需要干净的候选源码检出")
    build = read(checkout / "build/local-exe-selftest-receipt.json")
    if (build["source_commit"] != commit or build["self_test_exit"] != 0
            or build["exe_sha256"] != digest(checkout / "dist/Lazarus-Agent.exe")):
        raise ValueError("受测源码与便携构建回执不一致")
    for name in tuple(os.environ):
        if name.upper().startswith(("LAZARUS_", "REVIVER_MODEL_")):
            del os.environ[name]
    os.environ.update(LAZARUS_STORAGE_ROOT=str(output / "storage"), LAZARUS_MIGRATE_LEGACY="0")
    sys.path.insert(0, str(checkout / "tools"))
    import run_f01_p1 as benchmark
    import bench_device
    import feature_agent
    from model_tools import ModelToolError

    protocol_path = checkout / "tools/f01-p1c-cases.json"
    protocol = read(protocol_path)
    original = read(HISTORICAL)
    schedule = list(benchmark.trial_schedule(protocol))
    archived = {row["trial"]: row for row in original["rows"]}
    if len(schedule) != original["planned_trials"] or digest(protocol_path) != original["protocol_sha256"]:
        raise ValueError("冻结协议与历史批次不一致")
    planned = {}
    for case, mode, repeat in schedule:
        label = f"{case['id']}-{mode}-{repeat}"
        source_hash = original["source_apk_sha256"][case["id"]]
        raw = historical_trials / label
        if (digest(checkout / case["apk"]) != source_hash
                or digest(raw / "result.json") != archived[label]["raw_result_sha256"]):
            raise ValueError(f"历史试验或原包身份不符 {label}")
        row = {"case": case["id"], "class": case["class"], "mode": mode, "repeat": repeat,
               "source_sha256": source_hash, "historical_state": archived[label]["state"]}
        if mode in {"agent", "one-build"}:
            trace = read(raw / "agent-trace.json")
            result = read(raw / "result.json")["result"]
            if (trace["source_sha256"] != source_hash or trace["outcome"]["outcome"] != result["outcome"]
                    or [{k: item[k] for k in ("tool", "ok")} for item in trace["tools"]] != result["tool_trace"]):
                raise ValueError(f"历史轨迹与已归档试验不一致 {label}")
            row["historical_trace_sha256"] = digest(raw / "agent-trace.json")
        planned[label] = row
    plan = {"schema": 1, "scope": "offline-replay-of-historical-tool-decisions", "build": build,
            "historical_results_sha256": digest(HISTORICAL), "protocol_sha256": digest(protocol_path),
            "api": protocol["target"], "replay_tools_per_response": 2, "network_model_calls": 0, "trials": planned}
    output.mkdir(parents=True, exist_ok=True)
    plan_path = output / "plan.json"
    if plan_path.exists() and read(plan_path) != plan:
        raise ValueError("输出目录已有其他固定计划")
    if not bench_device.avd_ready(plan["api"]) or "emulator-5578" in subprocess.check_output([bench_device.ADB, "devices"], text=True):
        raise ValueError("Android 16 专用环境不可用")
    lock = output / "running.lock"
    with lock.open("x", encoding="ascii") as stream:
        stream.write(str(os.getpid()))
    if not plan_path.exists():
        benchmark.write_json(plan_path, plan)
    device = bench_device.Device(allow_existing=False)
    config = {"url": "http://127.0.0.1:1", "model": "recorded-tools", "key": "", "enabled": False}

    def update():
        rows = []
        for label, item in planned.items():
            receipt = output / label / "replay-receipt.json"
            rows.append(read(receipt) if receipt.exists() else {"trial": label, **item, "terminal": False,
                                                                "state": "unobserved"})
        benchmark.write_json(output / "cohort.json", {"scope": plan["scope"], "build": build,
                             "plan_sha256": digest(plan_path), "counts": summary(protocol, rows),
                             "network_model_calls": 0, "rows": rows})

    try:
        update()
        for case, mode, repeat in schedule:
            label = f"{case['id']}-{mode}-{repeat}"
            trial = output / label
            receipt = trial / "replay-receipt.json"
            if receipt.exists():
                continue
            if trial.exists():
                raise ValueError(f"上一轮留下未完成的试验目录 {label}")
            trial.mkdir()
            print("start", label, flush=True)
            original_trace = (read(historical_trials / label / "agent-trace.json")["tools"]
                              if mode in {"agent", "one-build"} else [])
            calls = tool_sequence(original_trace)
            index = 0

            def recorded_reply(url, payload, key, *, timeout, observe_usage=None):
                nonlocal index
                if url != config["url"] or payload["model"] != config["model"] or key or index == len(calls):
                    raise ModelToolError("离线轨迹结束或调用范围不符")
                batch = calls[index:index + 2]
                reply = {"tool_calls": [{"id": f"recorded-{index + offset}", "function": {
                          "name": name, "arguments": json.dumps(args, ensure_ascii=False)}}
                         for offset, (name, args) in enumerate(batch)]}
                index += len(batch)
                return reply

            with patch.object(feature_agent, "_request", side_effect=recorded_reply):
                result = benchmark.run_trial(case, mode, repeat, plan["api"], trial, device, config)
            if result["model_usage"]:
                raise RuntimeError("离线批次出现真实模型用量")
            proof_path = trial / "acceptance.json"
            proof = read(proof_path) if proof_path.exists() else {}
            trace_path = trial / "agent-trace.json"
            current_trace = read(trace_path)["tools"] if trace_path.exists() else []
            replay_outcome = result.get("result") or {}
            value = {"trial": label, **planned[label], "terminal": True, "state": score(case, result, proof),
                     "duration_seconds": result["duration_seconds"], "build_attempts": result["build_attempts"],
                     "candidate_status": result.get("candidate_status"), "candidate_sha256": replay_outcome.get("sha256"),
                     "tool_error": result.get("error"), "network_model_calls": 0,
                     "tool_sequence_matched": tool_sequence(current_trace) == calls if original_trace else None,
                     "tool_outcomes_matched": [row["ok"] for row in current_trace] == [row["ok"] for row in original_trace]
                     if original_trace else None,
                     "result_sha256": digest(trial / "result.json"),
                     "acceptance_sha256": digest(proof_path) if proof else None}
            benchmark.write_json(receipt, value)
            update()
            print("end", label, value["state"], value["tool_sequence_matched"], flush=True)
    finally:
        try:
            device.stop()
        finally:
            lock.unlink()


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    for field in ("checkout", "historical-trials", "output"):
        parser.add_argument("--" + field, type=Path, required=True)
    opts = parser.parse_args()
    replay(opts.checkout.resolve(), opts.historical_trials.resolve(), opts.output.resolve())
