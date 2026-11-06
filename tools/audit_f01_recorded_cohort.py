"""Independently check a completed offline F01 cohort and its retained APKs."""
from argparse import ArgumentParser
from dataclasses import replace
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.audit_f01_p1b import FrozenBatch, audit_records, git_at, p1c_batch
from tools import run_f01_p1, summarize_f01_p1
from tools.replay_f01_recorded_cohort import HISTORICAL, digest, plan_inputs, read, score, summary, tool_sequence


def score_trial(case, result, proof):
    return score(case, result, proof) if result else "unobserved"


def summarize_trials(results, protocol):
    with patch.object(summarize_f01_p1, "judge", score_trial):
        return summarize_f01_p1.summarize(results, protocol)


def audit(checkout, results, *, validated_build=None, expected_plan=None):
    if not results.is_relative_to(checkout / "build"):
        raise ValueError("复跑结果必须在受测检出的 build 下")
    if (results / "running.lock").exists():
        raise ValueError("请在批次停止后审核完整结果")
    plan_path = results / "plan.json"
    build = read(checkout / "build/local-exe-selftest-receipt.json")
    if validated_build is None:
        commit = git_at(checkout, "rev-parse", "HEAD").decode().strip()
        if git_at(checkout, "status", "--porcelain") or commit != build["source_commit"]:
            raise ValueError("受测源码与构建回执不一致")
        for name in ("agent_acceptance.py", "tools/run_f01_p1.py", "tools/summarize_f01_p1.py"):
            if digest(ROOT / name) != digest(checkout / name):
                raise ValueError(f"审核使用的验收代码与候选版本不一致 {name}")
        historical = p1c_batch(checkout)
    else:
        commit = validated_build["source_commit"]
        if build != validated_build:
            raise ValueError("离线源码工作区的构建回执已变化")
        original = read(HISTORICAL)
        historical = FrozenBatch(original["freeze_commit"], commit, build["version"],
                                 Path("tools/f01-p1c-cases.json"),
                                 Path("docs/science-superpowers/preregistrations/2026-11-06-f01-p1c.md"),
                                 results.relative_to(checkout))
    if build["self_test_exit"] != 0 or digest(checkout / "dist/Lazarus-Agent.exe") != build["exe_sha256"]:
        raise ValueError("受测源码或构建身份不一致")
    protocol, _, expected, traces = plan_inputs(checkout, run_f01_p1, build)
    cohort = read(results / "cohort.json")
    if ((expected_plan is not None and expected != expected_plan)
            or read(plan_path) != expected or cohort["plan_sha256"] != digest(plan_path)
            or cohort["build"] != build or cohort["scope"] != expected["scope"] or cohort["network_model_calls"] != 0):
        raise ValueError("复跑计划与固定输入或批次回执不一致")
    batch = replace(historical, implementation=commit, version=build["version"], results=results.relative_to(checkout))
    observed = audit_records(checkout, batch, {**protocol, "evaluation_role": expected["scope"]},
                             summarize_trials=summarize_trials, sidecar_directories=("storage",))
    rows = cohort["rows"]
    if (observed["model_responses_recorded"] != 0 or observed["terminal_trials"] != len(expected["trials"])
            or len(rows) != len(expected["trials"]) or cohort["counts"] != summary(protocol, rows)):
        raise ValueError("批次缺少终态、包含模型计量或汇总计数不符")
    for row, checked in zip(rows, observed["rows"]):
        label = checked["trial"]
        trial = results / label
        binding = expected["trials"][label]
        if (row["trial"] != label or row != read(trial / "replay-receipt.json")
                or any(row[key] != value for key, value in binding.items())
                or row["terminal"] is not True or row["network_model_calls"] != 0):
            raise ValueError(f"逐轮回执与计划不一致 {label}")
        for field in ("state", "candidate_sha256", "candidate_status", "build_attempts", "tool_error"):
            if row[field] != checked[field]:
                raise ValueError(f"逐轮回执与审核结果不一致 {label} {field}")
        if (row["result_sha256"] != checked["raw_result_sha256"]
                or row["acceptance_sha256"] != checked["raw_acceptance_sha256"]):
            raise ValueError(f"原始结果的 SHA-256 不一致 {label}")
        original = traces.get(label, {}).get("tools", [])
        trace_path = trial / "agent-trace.json"
        current = read(trace_path)["tools"] if trace_path.is_file() else []
        calls_match = tool_sequence(original) == tool_sequence(current) if original else None
        outcomes_match = [item["ok"] for item in original] == [item["ok"] for item in current] if original else None
        if row["tool_sequence_matched"] != calls_match or row["tool_outcomes_matched"] != outcomes_match:
            raise ValueError(f"重放工具轨迹与回执不一致 {label}")
        row["raw_diff_sha256"] = checked["raw_diff_sha256"]
    return {"schema": 1, "scope": expected["scope"], "build": build, "api": expected["api"],
            "protocol_sha256": expected["protocol_sha256"], "historical_results_sha256": expected["historical_results_sha256"],
            "plan_sha256": cohort["plan_sha256"], "counts": cohort["counts"], "network_model_calls": 0, "rows": rows}


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("checkout", type=Path)
    parser.add_argument("results", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.checkout.resolve(), args.results.resolve()), ensure_ascii=False, indent=2))
