"""Compare diagnostic actions on saved observations. Model access requires --live."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from model_advisor import advise


ACTIONS = ("test-original", "try-installation-floor-repair", "try-platform-http-library", "report-limit")
CASES = (
    ("release-replays/notes-34-itt1dp5c/report.json", "diagnosis", "try-installation-floor-repair"),
    ("release-replays/archive-35-mcacr3w_/report.json", "diagnosis", "try-platform-http-library"),
    ("release-replays/control-34-wmlh0cx6/report.json", "diagnosis", "test-original"),
    ("ttpod/repair.json", "diagnosis", "report-limit"),
    ("release-replays/wrong-expectation-35-vblnhy6s/report.json", "diagnosis", "report-limit"),
    ("release-replays/file-share-35-x1nj7vle/report.json", "candidate_diagnosis", "report-limit"),
)


def compare(live=False):
    if live and not all(os.environ.get(key) for key in ("REVIVER_MODEL_URL", "REVIVER_MODEL")):
        raise ValueError("Set REVIVER_MODEL_URL and REVIVER_MODEL before running --live")
    rows = []
    for path, stage, expected in CASES:
        record = json.loads((ROOT/"evidence"/path).read_text(encoding="utf-8"))[stage]
        evidence = record["evidence"]
        rules = record["advice"]
        if rules["source"] != "rules" or not set(rules["evidence_ids"]) <= evidence.keys():
            raise ValueError(f"Invalid offline baseline in {path}")
        row = {"report": path, "stage": stage, "evidence": evidence, "reference_action": expected,
               "recorded_rules_action": rules["action"], "model_reply": None, "model_match": None}
        if live:
            # Every case receives the same choices. Labels and historical advice stay local.
            reply = advise(evidence, list(ACTIONS), "report-limit")
            row["model_status"] = reply["source"] if reply["source"] == "model-tools" else reply["model_status"]
            if reply["source"] == "model-tools":
                row["model_reply"] = reply
                row["model_match"] = reply["action"] == expected
        rows.append(row)
    return {"mode": "live-replay" if live else "offline-baseline", "cases": rows,
            "valid_model_replies": sum(r["model_reply"] is not None for r in rows),
            "model_action_matches": sum(r["model_match"] is True for r in rows) if live else None}


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--live", action="store_true", help="Send only saved evidence fields to the configured model")
    cli.add_argument("--output", type=Path, help="Write a new JSON report instead of printing it")
    args = cli.parse_args()
    result = compare(args.live)
    report = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(report+"\n")
    else:
        print(report)
    if args.live and result["valid_model_replies"] != len(CASES):
        sys.exit(1)
