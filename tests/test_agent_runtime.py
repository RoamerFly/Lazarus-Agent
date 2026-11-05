from pathlib import Path
from subprocess import CompletedProcess
from unittest import TestCase
from unittest.mock import Mock, patch

from agent_device import AgentDevice
from agent_runtime import java_crashes


PACKAGE = "org.example.fixture"
METHOD = "edit()Landroid/content/SharedPreferences$Edit;"


def log(time, pid, package=PACKAGE, declared_pid=None):
    messages = [
        "FATAL EXCEPTION: main",
        f"Process: {package}, PID: {declared_pid or pid}",
        f"java.lang.NoSuchMethodError: No interface method {METHOD} in class Landroid/content/SharedPreferences; hidden payload",
        f"\tat {package}.MainActivity.click(MainActivity.java:39)",
        "\tat android.view.View.performClick(View.java:8083)",
    ]
    return "\n".join(f"11-05 {time} {pid} {pid} E AndroidRuntime: {message}" for message in messages)


class AgentRuntimeTests(TestCase):
    def test_parser_requires_exact_package_and_pid_and_emits_no_raw_message(self):
        target = log("16:50:23.713", 5188, PACKAGE + ":worker")
        other = log("16:50:23.712", 5187, PACKAGE + ".other")
        spoof = log("16:50:23.714", 5189, declared_pid=5188)
        reports = list(java_crashes("\n".join((other, target, spoof)), PACKAGE).values())
        self.assertEqual(len(reports), 1)
        report = reports[0]
        self.assertEqual(report["process"], PACKAGE + ":worker")
        self.assertEqual(report["exception_types"], ["java.lang.NoSuchMethodError"])
        self.assertEqual(report["missing_method"], {"owner": "Landroid/content/SharedPreferences;", "signature": METHOD})
        self.assertNotIn("hidden payload", repr(report))
        self.assertNotIn("android.view", repr(report))

    def test_action_reports_new_crash_even_when_ui_dump_failed(self):
        device = Mock()
        tester = AgentDevice(device, "task", 36, None, lambda _: None)
        tester.serial, tester.facts = "isolated", {"package": PACKAGE}
        old = log("16:49:23.713", 5100)
        new = log("16:50:23.713", 5188)
        with patch("agent_device.bench_device.invoke", side_effect=[
                CompletedProcess([], 0, old.encode(), b""),
                CompletedProcess([], 0, (old + "\n" + new).encode(), b""),
        ]) as adb, patch("agent_device.bench_device.shell"), patch("agent_device.time.sleep"), \
                patch.object(tester, "observe", side_effect=ValueError("界面树无效")):
            result = tester.action("back")
        self.assertEqual(adb.call_count, 2)
        self.assertIsNone(result["ui"])
        self.assertEqual(result["runtime_crash"]["app_frames"], [{"symbol": PACKAGE + ".MainActivity.click", "line": 39}])

    def test_same_previous_crash_and_unavailable_baseline_are_ignored(self):
        tester = AgentDevice(Mock(), "task", 36, None, lambda _: None)
        previous = java_crashes(log("16:50:23.713", 5188), PACKAGE)
        with patch.object(tester, "_crashes", return_value=previous) as crash_buffer:
            self.assertEqual(tester._new_crash(previous), {})
            self.assertEqual(tester._new_crash(None), {})
        crash_buffer.assert_called_once()

    def test_missing_class_only_recommends_libraries_listed_by_target(self):
        tester = AgentDevice(Mock(), "task", 36, None, lambda _: None)
        missing = {"process": PACKAGE, "missing_class": "org.apache.http.ProtocolVersion"}
        with patch.object(tester, "_crashes", return_value={("stamp", "12"): missing}), \
                patch("agent_device.bench_device.shell", side_effect=["library:org.apache.http.legacy", "library:other"]):
            runtime = tester._new_crash({})["runtime_crash"]
            self.assertEqual(runtime["platform_library"]["name"], "org.apache.http.legacy")
            self.assertNotIn("platform_library", tester._new_crash({})["runtime_crash"])

    def test_independent_replay_does_not_collect_model_diagnostics(self):
        device = Mock()
        device.ensure.return_value = "isolated"
        device.install_run.return_value = {"install": {"exit": 0}, "launch": {"exit": 0}, "abis": ["x86_64"]}
        tester = AgentDevice(device, "task", 36, None, lambda _: None)
        sha = "a" * 64
        facts = {"sha256": sha, "package": PACKAGE, "native_abis": []}
        with patch("agent_device.inspect_package", return_value=facts), \
                patch("agent_device.bench_device.shell", side_effect=["package:/data/app/app/base.apk", sha]), \
                patch.object(tester, "_crashes") as crash_buffer:
            result = tester.test(Path("test.apk"), sha, capture_ui=False)
        crash_buffer.assert_not_called()
        self.assertEqual(result["verification"], "install-and-launch")
        self.assertNotIn("runtime_crash", result)
