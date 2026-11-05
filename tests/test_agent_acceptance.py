import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

from agent_acceptance import parse_steps, verify, verified_for


def screen(package, text):
    root = ET.Element("hierarchy")
    ET.SubElement(root, "node", package=package, text="Open", bounds="[0,0][20,20]")
    ET.SubElement(root, "node", package=package, text=text)
    return root


class AcceptanceTests(unittest.TestCase):
    def test_replay_rejects_plan_without_positive_assertion(self):
        with self.assertRaisesRegex(ValueError, "预期"):
            parse_steps({"steps": [{"action": "cold_start"}, {"action": "absent", "value": "Error"}]})
        with self.assertRaises(ValueError):
            parse_steps({"steps": [{"action": "input", "value": "a; reboot"}, {"action": "expect", "value": "OK"}]})

    def test_same_candidate_partial_scope_and_wrong_owner_cannot_pass(self):
        steps = parse_steps({"tap": "Open", "expect": "Expected"})
        digest = hashlib.sha256(b"candidate").hexdigest()
        baseline_digest = hashlib.sha256(b"original").hexdigest()
        package = "example.app"
        install = {"exit": 0}
        launch = {"exit": 0, "output": "Status: ok"}

        def tester(api):
            return SimpleNamespace(
                facts={"package": package}, device=Mock(), serial="test", owner="job",
                test=Mock(side_effect=lambda apk, sha, **kwargs: {
                    "install": install, "launch": launch, "installed_sha256": sha,
                }),
                tree=Mock(side_effect=[screen(package, "Before")]*2 + [screen(package, "Old")] +
                          [screen(package, "Before")]*2 +
                          [screen(package if api == 36 else "other.app", "Expected")]),
            )

        with TemporaryDirectory() as temporary, patch("agent_acceptance.bench_device.shell"), patch("agent_acceptance.time.sleep"):
            base, apk = [Path(temporary) / name for name in ("base.apk", "candidate.apk")]
            base.write_bytes(b"original")
            apk.write_bytes(b"candidate")
            report = verify(base, baseline_digest, apk, digest, [36, 35], steps, tester, lambda api: True, lambda _: None)
            self.assertEqual(report["status"], "incomplete")
            self.assertEqual([r["status"] for r in report["rows"]], ["behavior-verified", "failed"])
            self.assertFalse(verified_for(report, digest))
            passed = verify(base, baseline_digest, apk, digest, [36], steps, tester, lambda api: True, lambda _: None)
            self.assertTrue(verified_for(passed, digest))
            self.assertEqual(passed["rows"][0]["comparison"], "baseline-check-failed")
            passed["rows"][0]["candidate"]["device"]["installed_sha256"] = baseline_digest
            self.assertFalse(verified_for(passed, digest))
