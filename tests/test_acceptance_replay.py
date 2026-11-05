import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tools import replay_agent_acceptance as replay


class AcceptanceReplayTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.base, self.candidate = root / "source.apk", root / "candidate.apk"
        self.base.write_bytes(b"source")
        self.candidate.write_bytes(b"candidate")
        self.report, self.result, self.log = root / "report.json", root / "result.json", root / "log.txt"
        self.expected = {"schema": 1, "targets": [36], "steps": [{"action": "expect", "value": "Done"}],
                         "input_sha256": replay.digest_file(self.base),
                         "candidate_sha256": replay.digest_file(self.candidate)}

    def run_check(self):
        self.report.write_text(json.dumps(self.expected), encoding="utf-8")
        return replay.main([str(self.report), str(self.base), str(self.candidate),
                            "--output", str(self.result), "--log", str(self.log), "--check-only"])

    def test_check_only_checks_hashes_without_initializing_device(self):
        with patch.object(replay.bench_device, "Device") as device:
            self.assertEqual(self.run_check(), 0)
            device.assert_not_called()
        result = json.loads(self.result.read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "inputs-verified")
        self.assertEqual(result["steps"], self.expected["steps"])

    def test_candidate_changed_after_report_is_rejected(self):
        self.candidate.write_bytes(b"tampered")
        self.assertEqual(self.run_check(), 2)
        self.assertFalse(self.result.exists())
        self.assertIn("哈希不符", self.log.read_text(encoding="utf-8"))

    def test_missing_targets_does_not_default_to_api_36(self):
        del self.expected["targets"]
        self.assertEqual(self.run_check(), 2)
        self.assertFalse(self.result.exists())
        self.assertIn("缺少目标", self.log.read_text(encoding="utf-8"))
