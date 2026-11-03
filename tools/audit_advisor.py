"""Replay captured evidence and exercise the adapter contract without any network calls."""
from io import BytesIO
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from model_advisor import advise


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
    with patch.dict(os.environ, {}, clear=True), patch("model_advisor.urlopen") as transport:
        for stem, expected in cases.items():
            diagnosis = json.loads((reports / (stem + "-report.json")).read_text(encoding="utf-8"))["diagnosis"]
            evidence = diagnosis["evidence"]
            assert diagnosis["advice"]["action"] == expected
            reply = advise(evidence, [expected], expected)
            assert reply["source"] == "rules" and reply["action"] == expected
            assert reply["model_status"] == "not-configured"
            assert reply["evidence_ids"] and set(reply["evidence_ids"]) <= evidence.keys()
            lines.append(stem + ": offline evidence-linked explanation verified")
        transport.assert_not_called()
    evidence = json.loads((reports/"oi-api35-report.json").read_text(encoding="utf-8"))["diagnosis"]["evidence"]
    action = cases["oi-api35"]
    allowed = [action, "report-limit"]
    env = {"REVIVER_MODEL_URL":"http://localhost:1/v1","REVIVER_MODEL":"contract-fixture"}
    def check(raw, source, selected):
        content = json.dumps({"choices":[{"message":{"content":raw}}]}).encode()
        with patch("model_advisor.urlopen", return_value=BytesIO(content)) as mock:
            reply = advise(evidence, allowed, action)
            req = mock.call_args.args[0]
            sent = json.loads(json.loads(req.data)["messages"][1]["content"])
            assert sent == {"evidence":evidence,"allowed":allowed}
            assert reply["source"] == source and reply["action"] == selected
    with patch.dict(os.environ, env, clear=True):
        suggestion = {"action":"report-limit","reason":"复核安装错误","evidence_ids":["E5_install"]}
        check(json.dumps(suggestion), "model", "report-limit")
        check(json.dumps({**suggestion, "action":"execute-shell"}), "rules", action)
        check(json.dumps({**suggestion, "evidence_ids":["made-up"]}), "rules", action)
        check("malformed", "rules", action)
        with patch("model_advisor.urlopen", side_effect=TimeoutError):
            reply = advise(evidence, allowed, action)
            assert reply["model_status"] == "fallback:TimeoutError" and reply["action"] == action
    lines.append("in-memory fixtures: valid response, disallowed action, unknown evidence, malformed JSON, timeout verified")
    return lines


if __name__ == "__main__":
    print("\n".join(verify()))
