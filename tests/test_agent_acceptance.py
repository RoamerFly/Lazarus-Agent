import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

from agent_acceptance import _step, _verified_step, _wait_for_launch_ui, parse_steps, rehearse_candidate, verify, verified_for
from agent_device import AgentDevice


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


def focused_editor(text="0", **attributes):
    root = ET.Element("hierarchy")
    field = {"package": "example.app", "class": "android.widget.EditText", "resource-id": "example.app:id/value",
             "focused": "true", "enabled": "true", "password": "false", "text": text}
    ET.SubElement(root, "node", field | attributes)
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

    def test_replace_step_checks_focused_field_and_records_exact_result(self):
        value = "2147483646"
        step = {"action": "replace_input", "value": value}
        self.assertEqual(parse_steps({"steps": [step, {"action": "expect", "value": value}]})[0], step)
        tester = AgentDevice(Mock(), "job", 36, None, None)
        tester.facts = {"package": "example.app"}
        tester.serial = "test"
        tester.tree = Mock(side_effect=[focused_editor()] * 3 + [focused_editor(value)])
        result = {}
        with patch("agent_device.bench_device.shell") as shell:
            _step(tester, step, result, set())
        self.assertEqual([c.args[1:] for c in shell.call_args_list],
                         [("input", "keycombination", "KEYCODE_CTRL_LEFT", "KEYCODE_A"), ("input", "text", value)])
        self.assertEqual(result["field"], {"resource-id": "example.app:id/value", "text": value})
        evidence = {**step, **result, "status": "passed"}
        self.assertTrue(_verified_step(evidence, step))
        del evidence["field"]
        self.assertFalse(_verified_step(evidence, step))

    def test_replace_input_rejects_password_or_foreign_focus(self):
        tester = AgentDevice(Mock(), "job", 36, None, None)
        tester.facts = {"package": "example.app"}
        for tree in (focused_editor(password="true"), focused_editor(package="other.app")):
            tester.tree = Mock(return_value=tree)
            with patch("agent_device.bench_device.shell") as shell, self.assertRaises(ValueError):
                tester.replace_input("123")
            shell.assert_not_called()

    def test_replace_input_stops_after_focus_change_and_rejects_truncation(self):
        tester = AgentDevice(Mock(), "job", 36, None, None)
        tester.facts = {"package": "example.app"}
        tester.tree = Mock(side_effect=[focused_editor(), focused_editor(**{"resource-id": "example.app:id/other"})])
        with patch("agent_device.bench_device.shell") as shell, self.assertRaisesRegex(ValueError, "焦点"):
            tester.replace_input("123")
        self.assertEqual(shell.call_count, 1)
        tester.tree = Mock(side_effect=[focused_editor(), focused_editor(), focused_editor("12")])
        with patch("agent_device.bench_device.shell"), self.assertRaisesRegex(ValueError, "未显示完整"):
            tester.replace_input("123")

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
            self.assertEqual(passed["coverage"], {"specified_ui_steps": 2, "external_app_calls": "not-tested"})
            self.assertEqual(passed["rows"][0]["comparison"], "baseline-check-failed")
            passed["rows"][0]["candidate"]["device"]["installed_sha256"] = baseline_digest
            self.assertFalse(verified_for(passed, digest))

    def test_candidate_download_binds_legacy_report_to_the_original_bytes(self):
        from fastapi.testclient import TestClient
        import workbench

        job, run = "a" * 32, "b" * 32
        sha256 = hashlib.sha256(b"candidate").hexdigest()
        with TemporaryDirectory() as temporary, patch.object(workbench, "STORAGE", Path(temporary)):
            folder = Path(temporary) / job
            apk = folder / "feature-candidates" / run / "candidate.apk"
            apk.parent.mkdir(parents=True)
            apk.write_bytes(b"candidate")
            report = {"steps": [{"action": "expect", "value": "Open"}]}
            workbench.save(folder, {"api": 36, "package": {"package": "example.app"},
                                    "feature_candidates": {run: {"sha256": sha256, "acceptance": report}}})
            url = f"/api/jobs/{job}/feature-candidates/{run}"
            with TestClient(workbench.app, base_url=f"http://127.0.0.1:{workbench.PORT}") as client:
                self.assertEqual(client.get(url + "/acceptance").json()["coverage"],
                                 {"specified_ui_steps": 1, "external_app_calls": "not-tested"})
                self.assertEqual(client.get(url).content, b"candidate")
                apk.write_bytes(b"changed")
                self.assertEqual(client.get(url).status_code, 409)
