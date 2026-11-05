import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

from agent_acceptance import _step, _wait_for_launch_ui, parse_steps, rehearse_candidate, verify, verified_for


def screen(package, text):
    root = ET.Element("hierarchy")
    ET.SubElement(root, "node", package=package, text="Open", bounds="[0,0][20,20]")
    ET.SubElement(root, "node", package=package, text=text)
    return root


def dialog_screen(package="android", title="System UI isn't responding"):
    root = screen("example.app", "Background target")
    ET.SubElement(root, "node", {"package": package, "text": title, "resource-id": "android:id/alertTitle"})
    ET.SubElement(root, "node", {"package": package, "text": "Wait", "resource-id": "android:id/aerr_wait",
                                 "bounds": "[0,40][40,60]"})
    return root


class AcceptanceTests(unittest.TestCase):
    def test_launch_wait_requires_observed_target_ui(self):
        tester = SimpleNamespace(facts={"package": "example.app"},
                                 tree=Mock(side_effect=[screen("other.app", "Loading"),
                                                        screen("example.app", "Ready")]),
                                 window_context=Mock(return_value={"display_fields": ["mCurrentFocus=null"]}))
        row = {}
        with patch("agent_acceptance.time.sleep"):
            self.assertTrue(_wait_for_launch_ui(tester, row, set()))
        self.assertEqual([r["target_nodes"] for r in row["launch_observations"]], [0, 2])
        self.assertEqual(row["launch_observations"][0]["window_owners"], {"other.app": 2})
        tester.tree = Mock(return_value=screen("other.app", "Ready"))
        with patch("agent_acceptance.time.sleep"):
            self.assertFalse(_wait_for_launch_ui(tester, row, set()))
        self.assertEqual(tester.tree.call_count, 8)
        self.assertEqual(row["launch_window"], {"display_fields": ["mCurrentFocus=null"]})

    def test_launch_wait_records_transient_tree_failure(self):
        tester = SimpleNamespace(facts={"package": "example.app"},
                                 tree=Mock(side_effect=[ValueError("界面树无效"), screen("example.app", "Ready")]))
        row = {}
        with patch("agent_acceptance.time.sleep"):
            self.assertTrue(_wait_for_launch_ui(tester, row, set()))
        self.assertIn("error", row["launch_observations"][0])

    def test_system_wait_records_blocker_then_requires_new_target_frame(self):
        tester = SimpleNamespace(facts={"package": "example.app"}, serial="test",
                                 tree=Mock(side_effect=[dialog_screen(), screen("example.app", "Ready")]))
        row = {}
        with patch("agent_acceptance.bench_device.shell") as shell, patch("agent_acceptance.time.sleep"):
            self.assertTrue(_wait_for_launch_ui(tester, row, set()))
        shell.assert_called_once_with("test", "input", "tap", "20", "50")
        blocked, ready = row["launch_observations"]
        self.assertEqual(blocked["system_dialog"], "system-ui-anr")
        self.assertEqual(blocked["dialog_title"], "System UI isn't responding")
        self.assertEqual(blocked["dialog_action"], "wait")
        self.assertNotIn("system_dialog", ready)

    def test_anr_in_front_of_app_never_counts_as_launch(self):
        tester = SimpleNamespace(facts={"package": "example.app"}, serial="test",
                                 tree=Mock(return_value=dialog_screen(title="Example isn't responding")),
                                 window_context=Mock(return_value={"display_fields": []}))
        row = {}
        with patch("agent_acceptance.bench_device.shell") as shell:
            self.assertFalse(_wait_for_launch_ui(tester, row, set()))
        shell.assert_not_called()
        self.assertEqual(row["launch_observations"][0]["system_dialog"], "other-anr")

    def test_cold_start_step_preserves_evidence_when_system_prompt_persists(self):
        tester = SimpleNamespace(facts={"package": "example.app", "launcher": ".Main"}, serial="test",
                                 device=Mock(), owner="job", tree=Mock(return_value=dialog_screen()),
                                 window_context=Mock(return_value={"display_fields": []}))
        row = {}
        with patch("agent_acceptance.bench_device.shell", return_value="Status: ok") as shell, patch("agent_acceptance.time.sleep"):
            with self.assertRaisesRegex(ValueError, "未观察到目标应用页面"):
                _step(tester, {"action": "cold_start"}, row, {"system-ui-anr"})
        self.assertEqual([call.args[1] for call in shell.call_args_list], ["am", "am"])
        self.assertTrue(all(frame["system_dialog"] == "system-ui-anr" for frame in row["launch_observations"]))

    def test_replay_rejects_plan_without_positive_assertion(self):
        with self.assertRaisesRegex(ValueError, "预期"):
            parse_steps({"steps": [{"action": "cold_start"}, {"action": "absent", "value": "Error"}]})
        with self.assertRaises(ValueError):
            parse_steps({"steps": [{"action": "input", "value": "a; reboot"}, {"action": "expect", "value": "OK"}]})

    def test_rehearsal_returns_first_failed_step_and_target_app_evidence(self):
        steps = parse_steps({"steps": [{"action": "tap", "value": "Open"},
                                       {"action": "expect", "value": "Expected"},
                                       {"action": "expect", "value": "Later"}]})
        sha256 = hashlib.sha256(b"candidate").hexdigest()
        tree = screen("example.app", "Old")
        ET.SubElement(tree, "node", package="other.app", text="Expected")
        runtime = {"runtime_crash": {"exception_types": ["java.lang.NoSuchMethodError"]}}
        tester = SimpleNamespace(
            api=36,
            facts={"package": "example.app"}, device=Mock(), serial="test", owner="job",
            test=Mock(return_value={"install": {"exit": 0}, "launch": {"exit": 0, "output": "Status: ok"},
                                    "installed_sha256": sha256}),
            tree=Mock(return_value=tree),
            crash_snapshot=Mock(return_value={}),
            new_crash=Mock(return_value=runtime),
        )
        with TemporaryDirectory() as temporary, patch("agent_acceptance.bench_device.shell"), patch("agent_acceptance.time.sleep"):
            apk = Path(temporary) / "candidate.apk"
            apk.write_bytes(b"candidate")
            report = rehearse_candidate(tester, apk, sha256, steps)
        self.assertEqual(report["verification"], "candidate-rehearsal")
        self.assertEqual(report["api"], 36)
        self.assertEqual(report["installed_sha256"], sha256)
        self.assertEqual(report["runtime_crash"], runtime["runtime_crash"])
        tester.new_crash.assert_called_once_with({})
        self.assertEqual((report["function"], report["passed_steps"], report["expected_steps"]), ("failed", 1, 3))
        self.assertEqual(report["failed_step"], {"index": 2, "action": "expect", "value": "Expected",
                                                 "match_count": 0, "observed": [{"text": "Open"}, {"text": "Old"}]})
        self.assertFalse(verified_for(report, sha256))

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
