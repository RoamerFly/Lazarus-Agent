"""Summarize the fixed F01 schedule without dropping missing or failed trials."""
from argparse import ArgumentParser
import json
from pathlib import Path

from run_f01_p1 import PROTOCOL, trial_schedule


def load(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def judge(case, result, proof):
    if not result or result.get("error"):
        return "unobserved"
    if case["class"] == "positive":
        if not result.get("behavior_verified"):
            return "failed"
        if result.get("comparison") not in {"baseline-check-failed", "baseline-install-failed"}:
            return "unattributed"
        return "passed"
    if not result.get("model_usage") or not result.get("result"):
        return "unobserved"
    if result.get("behavior_verified"):
        return "false-acceptance"
    if result["result"].get("sha256"):
        rows = proof.get("rows", [])
        if len(rows) != 1 or rows[0].get("status") != "failed":
            return "unobserved"
        baseline = rows[0].get("baseline", {})
    else:
        baseline = result.get("baseline", {})
    install_error = (baseline.get("device") or {}).get("install", {}).get("output", "")
    if case["id"] == "N01":
        observed = baseline.get("install") == "failed" and "NO_MATCHING_ABIS" in install_error
    else:
        observed = baseline.get("function") == "failed"
    return "rejected" if observed else "unobserved"


def price_bounds(responses):
    """Return USD range from the official off-peak / peak Flash prices."""
    if not responses:
        return None
    fields = ("prompt_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens", "completion_tokens")
    if any(any(field not in row for field in fields) or
           row[fields[0]] != row[fields[1]] + row[fields[2]] for row in responses):
        return None
    off_peak = sum(row[fields[1]] * 0.003 + row[fields[2]] * 0.15 + row[fields[3]] * 0.6
                   for row in responses) / 1_000_000
    return [round(off_peak, 8), round(2 * off_peak, 8)]


def summarize(root, protocol):
    rows = []
    for case, mode, repeat in trial_schedule(protocol):
        trial = root / f"{case['id']}-{mode}-{repeat}"
        result = load(trial / "result.json")
        usage = result.get("model_usage", load(trial / "usage.json"))
        state = judge(case, result, load(trial / "acceptance.json"))
        trace = load(trial / "agent-trace.json").get("tools", [])
        translation_attempt = any(tool["tool"] == "localize_resources" for tool in trace)
        rows.append({"case": case["id"], "class": case["class"], "mode": mode, "repeat": repeat,
                     "state": state, "started": (trial / "started.json").exists(),
                     "duration_seconds": result.get("duration_seconds"),
                     "build_attempts": result.get("build_attempts"),
                     "model_requests_with_usage": len(usage),
                     "model_price_usd_range": None if translation_attempt else price_bounds(usage)})
    positives = [row for row in rows if row["mode"] == "agent" and row["class"] == "positive"]
    negatives = [row for row in rows if row["mode"] == "agent" and row["class"] == "negative"]
    counts = {"agent_positives": sum(row["state"] == "passed" for row in positives),
              "primary": sum(row["case"] == protocol["primary"] and row["state"] == "passed" for row in positives),
              "agent_negative_rejected": sum(row["state"] == "rejected" for row in negatives),
              "agent_false_acceptances": sum(row["state"] == "false-acceptance" for row in negatives),
              "unobserved": sum(row["state"] == "unobserved" for row in rows),
              "majority_agent_cases": sum(sum(row["case"] == case["id"] and row["state"] == "passed"
                                             for row in positives) >= 2 for case in protocol["cases"]
                                          if case["class"] == "positive")}
    for mode in ("roundtrip", "rules", "one-build"):
        counts[mode + "_positive_cases"] = sum(row["mode"] == mode and row["state"] == "passed" for row in rows)
    return {"counts": counts, "rows": rows}


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("--protocol", type=Path, default=PROTOCOL, help="Case manifest used by the runner")
    args = parser.parse_args()
    spec = json.loads(args.protocol.read_text(encoding="utf-8-sig"))
    print(json.dumps(summarize(args.results, spec), ensure_ascii=False, indent=2))
