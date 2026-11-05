"""Reject edits not backed by the selected APK's tool evidence."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from feature_agent import EDIT, INSPECT, run
from model_tools import ModelToolError


class FeatureAgentTests(unittest.TestCase):
    def test_unobserved_activity_cannot_reach_builder(self):
        def call(call_id, name, **args):
            return {"role": "assistant", "tool_calls": [{"id": call_id, "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)}}]}

        config = {"url": "http://localhost:8000", "model": "test", "key": ""}
        evidence = {"source_sha256": "original", "inspection": {"activities": [
            {"activity": "example.Editor"},
        ]}}
        with (
            patch("feature_agent.inspect_package", return_value={"sha256": "original", "package": "example"}),
            patch("feature_agent.inspect_decoded", return_value=evidence),
            patch("feature_agent._request", side_effect=[
                call("read", INSPECT), call("write", EDIT, activity="example.Other", orientation="landscape"),
            ]) as request,
            patch("feature_agent._build") as build,
        ):
            with self.assertRaises(ModelToolError):
                run(Path("input.apk"), Path("candidate.apk"), Path("tools"), 36, "original",
                    "Edit in landscape", config, lambda _: None)
        build.assert_not_called()
        messages = request.call_args.args[1]["messages"]
        self.assertEqual(messages[-1]["tool_call_id"], "read")
        self.assertEqual(json.loads(messages[-1]["content"]), evidence["inspection"])
