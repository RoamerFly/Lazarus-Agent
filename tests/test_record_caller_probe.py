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
        timeline.forged.return_value = {"outcome": "target-rejected", "observed": [(probe.TARGET, "Rejected")]}
        uid_rows = [f"package:{probe.TARGET} uid:10001", f"package:{probe.CALLER} uid:10002"]
        with (patch.object(probe, "AgentDevice", side_effect=[target, caller]),
              patch.object(probe, "_wait_for_launch_ui", return_value=True),
              patch.object(probe, "digest_file", return_value=caller_hash),
              patch.object(probe.bench, "shell", side_effect=uid_rows),
              patch.object(probe, "send_known_uri", timeline.external),
              patch.object(probe, "send_probe", timeline.forged),
              patch.object(probe, "_step", timeline.step)):
            result = probe.replay(Mock(), Path("candidate.apk"), expected, Path("caller.apk"),
                                  compare_uri=True, record_path="/record", forged_bytes_after_share=True)
        self.assertEqual([call[0] for call in timeline.mock_calls], ["external", "step", "step", "step", "external", "forged"])
        self.assertEqual(timeline.external.call_args_list[0], timeline.external.call_args_list[1])
        timeline.external.assert_called_with(caller, "content://org.ggbond.archivereader.records/record")
        self.assertEqual(result["before_share"], before)
        self.assertEqual(result["outcome"], after["outcome"])
        self.assertEqual(result["observed"], after["observed"])
        self.assertEqual(result["record_uri"], "content://org.ggbond.archivereader.records/record")
        self.assertEqual([step["action"] for step in result["in_app_share"]], ["cold_start", "tap", "expect"])
        timeline.forged.assert_called_once_with(caller, "--ez", "probe_bytes", "true")
        self.assertEqual(result["forged_bytes_after"], timeline.forged.return_value)

    def test_rejection_text_is_bound_to_target_package(self):
        nodes = [(probe.TARGET, "Rejected")]
        with patch.object(probe, "wait_for", return_value=nodes):
            self.assertEqual(probe.observe_outcome(Mock())["outcome"], "target-rejected")
        with patch.object(probe, "wait_for", return_value=[(probe.CALLER, "Rejected")]):
            with self.assertRaises(ValueError):
                probe.observe_outcome(Mock())

    def test_synthetic_application_scheme_is_accepted(self):
        self.assertIsNotNone(probe.RECORD_URI.fullmatch("ggbond-record://local/record.txt"))
        self.assertIsNone(probe.RECORD_URI.fullmatch("file://local/record.txt"))

    def test_known_uri_passes_exact_authority_and_path_to_the_separate_app(self):
        caller = Mock()
        caller.serial = "emulator-5578"
        uri = "content://org.ggbond.archivereader.share/private/entry.txt"
        with (patch.object(probe.bench, "shell") as adb,
              patch.object(probe, "observe_outcome", return_value={"outcome": "forged-success"})):
            result = probe.send_known_uri(caller, uri)
        adb.assert_called_with(caller.serial, "am", "start", "-W", "-n", probe.CALLER + "/.BoundaryActivity",
                               "--es", "record_uri", uri)
        self.assertEqual(result["outcome"], "external-record-status")

    def test_rejects_shell_significant_characters_in_a_record_uri(self):
        with self.assertRaisesRegex(ValueError, "literal authority and path"):
            probe.replay(Mock(), Path("candidate.apk"), "a" * 64, Path("caller.apk"),
                         compare_uri=True, record_uri="content://archive/record;echo")
