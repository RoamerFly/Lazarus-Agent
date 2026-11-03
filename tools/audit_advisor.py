"""Replay saved evidence against the bounded model-tool contract."""
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from model_advisor import advise


def _reply(name, arguments, call_id):
    return {"role": "assistant", "tool_calls": [{"id": call_id, "function": {
        "name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}]}


def _tool_replies(evidence, action):
    runtime = {key: value for key, value in evidence.items()
               if key.startswith(("E2_", "E4_", "E5_", "E6_", "E7_", "E8_", "E9_", "E13_"))}
    manifest = {key: value for key, value in evidence.items()
                if key.startswith(("E1_", "E3_", "E10_", "E11_", "E12_"))}
    source = "read_runtime_summary" if runtime else "read_manifest_summary"
    ids = list((runtime or manifest).keys())[:1]
    return iter([_reply(source, {}, "read-1"),
                 _reply("select_repair", {"action": action, "reason": "回放已读取证据。", "evidence_ids": ids}, "select-1")])


def verify():
    reports = ROOT / "evidence" / "peer-web"
    cases = {
        "oi-api35": "try-installation-floor-repair",
        "archive-broken-api35": "try-platform-http-library",
        "archive-control-api34": "test-original",
        "google-my-tracks-api35-limit": "report-limit",
        "archive-control-api34-wrong-expectation": "report-limit",
    }
    lines = []
    with patch("model_advisor.get_config", return_value={"enabled": False, "url": "", "model": "", "key": ""}):
        for stem, expected in cases.items():
            diagnosis = json.loads((reports / (stem + "-report.json")).read_text(encoding="utf-8"))["diagnosis"]
            evidence = diagnosis["evidence"]
            assert diagnosis["advice"]["action"] == expected
            reply = advise(evidence, [expected], expected)
            assert reply["source"] == "rules" and reply["action"] == expected
            assert reply["model_status"] == "not-configured"
            assert reply["evidence_ids"] and set(reply["evidence_ids"]) <= evidence.keys()
            lines.append(stem + ": offline evidence-linked explanation verified")
    evidence = json.loads((reports / "oi-api35-report.json").read_text(encoding="utf-8"))["diagnosis"]["evidence"]
    action = cases["oi-api35"]
    config = {"enabled": True, "url": "http://localhost:1/v1", "model": "contract-fixture", "key": ""}
    with patch("model_advisor.get_config", return_value=config):
        replies = _tool_replies(evidence, "report-limit")
        with patch("model_tools._request", side_effect=lambda *_args: next(replies)):
            reply = advise(evidence, [action, "report-limit"], action)
        assert reply["source"] == "model-tools" and reply["action"] == "report-limit"
        assert reply["evidence_ids"] == ["E2_device_api"]
        bad = iter([_reply("read_runtime_summary", {}, "read-1"),
                    _reply("select_repair", {"action": "execute-shell", "reason": "bad", "evidence_ids": ["E5_install"]}, "select-1")])
        with patch("model_tools._request", side_effect=lambda *_args: next(bad)):
            reply = advise(evidence, [action, "report-limit"], action)
        assert reply["source"] == "rules" and reply["model_status"] == "fallback:ModelToolError"
    lines.append("tool-call fixtures: read evidence, allowed action, unknown action and deterministic fallback verified")
    return lines


if __name__ == "__main__":
    print("\n".join(verify()))
