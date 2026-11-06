from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from tools import replay_record_caller as probe


class RecordCallerProbeTests(unittest.TestCase):
    def test_compares_the_same_external_uri_around_a_real_in_app_share(self):
        expected = "a" * 64
        caller_hash = "b" * 64
        target, caller = Mock(), Mock()
        target.serial = caller.serial = "emulator-5578"
        target.test.return_value = {"installed_sha256": expected, "abis": ["x86_64"]}
        caller.test.return_value = {"installed_sha256": caller_hash}
        before = {"outcome": "target-rejected", "observed": [(probe.TARGET, "Cannot read record")]}
        after = {"outcome": "external-record-status", "observed": [(probe.TARGET, "Shared")]}
        timeline = Mock()
        timeline.external.side_effect = [before, after]
        uid_rows = [f"package:{probe.TARGET} uid:10001", f"package:{probe.CALLER} uid:10002"]
        with (patch.object(probe, "AgentDevice", side_effect=[target, caller]),
              patch.object(probe, "_wait_for_launch_ui", return_value=True),
              patch.object(probe, "digest_file", return_value=caller_hash),
              patch.object(probe.bench, "shell", side_effect=uid_rows),
              patch.object(probe, "send_known_uri", timeline.external),
              patch.object(probe, "_step", timeline.step)):
            result = probe.replay(Mock(), Path("candidate.apk"), expected, Path("caller.apk"), compare_uri=True)
        self.assertEqual([call[0] for call in timeline.mock_calls], ["external", "step", "step", "step", "external"])
        self.assertEqual(timeline.external.call_args_list[0], timeline.external.call_args_list[1])
        self.assertEqual(result["before_share"], before)
        self.assertEqual(result["outcome"], after["outcome"])
        self.assertEqual(result["observed"], after["observed"])
        self.assertEqual([step["action"] for step in result["in_app_share"]], ["cold_start", "tap", "expect"])
