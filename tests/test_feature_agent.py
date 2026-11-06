"""Regression checks for generic editing, failed builds, and evidence gates."""
from contextlib import nullcontext
import json
from pathlib import Path
from subprocess import TimeoutExpired
import tempfile
import unittest
from unittest.mock import Mock, patch

from agent_workspace import Workspace, digest
from feature_agent import Session, _loop, run, tool_definitions


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "AndroidManifest.xml").write_text("<manifest/>", encoding="utf-8")
        self.ws = Workspace(self.root)

    def test_escape_and_unread_edits_are_rejected(self):
        for name in ("../outside.txt", "C:/secret.txt", "/absolute", "assets/../../secret", "a\\b"):
            with self.assertRaises(ValueError):
                self.ws.path(name)
        with self.assertRaises(ValueError):
            self.ws.replace_text("AndroidManifest.xml", digest(b"<manifest/>"), "<manifest/>", "<manifest />")

    def test_stale_hash_cannot_overwrite_and_restore_works(self):
        row = self.ws.read_text("AndroidManifest.xml")
        self.ws.replace_text("AndroidManifest.xml", row["sha256"], "<manifest/>", "<manifest />")
        with self.assertRaises(ValueError):
            self.ws.replace_text("AndroidManifest.xml", row["sha256"], "<manifest />", "<manifest/>")
        self.ws.restore_file("AndroidManifest.xml")
        self.assertEqual(self.ws.changes(), [])

    def test_sliced_edits_preserve_windows_line_endings(self):
        path = self.root / "AndroidManifest.xml"
        path.write_bytes(b"<manifest>\r\n  <first/>\r\n  <second/>\r\n</manifest>\r\n")
        row = self.ws.read_text(path.name)
        first = self.ws.replace_text(path.name, row["sha256"], "  <first/>\n  <second/>",
                                     "  <updated/>\n  <second/>")
        self.ws.read_text(path.name, 3, 1)
        self.ws.replace_lines(path.name, first["sha256"], 3, 3, "  <last/>")
        self.assertEqual(path.read_bytes(), b"<manifest>\r\n  <updated/>\r\n  <last/>\r\n</manifest>\r\n")

    def test_new_smali_and_resource_files_share_the_same_tools(self):
        self.ws.create_text_file("smali/example/Helper.smali", ".class public Lexample/Helper;")
        self.ws.create_text_file("res/values/added.xml", "<resources/>")
        self.assertEqual(len(self.ws.changes()), 2)
        self.assertEqual(self.ws.search_text("Helper")["hits"][0]["path"], "smali/example/Helper.smali")
        self.ws.restore_file("res/values/added.xml")
        self.assertFalse((self.root / "res/values/added.xml").exists())

    def test_current_revision_must_be_built_before_publishing(self):
        session = Session(self.root, Path("source.apk"), self.root/"candidate.apk",
                          Path("tools"), 36, {}, lambda _: None, None)
        with self.assertRaises(ValueError):
            session.finish("candidate", "Done")
        artifact = self.root/"built.apk"
        artifact.write_bytes(b"signed")
        session.artifact = artifact
        session.build_report = {"sha256": digest(b"signed")}
        session.built_revision = 0
        self.ws = session.workspace
        row = self.ws.read_text("AndroidManifest.xml")
        self.ws.replace_text("AndroidManifest.xml", row["sha256"], "<manifest/>", "<manifest />")
        with self.assertRaises(ValueError):
            session.finish("candidate", "Done")

    def test_manifest_inspection_binds_original_and_current_candidate(self):
        original = self.root / "original.apk"
        original.write_bytes(b"original")
        candidate = self.root / "candidate.apk"
        candidate.write_bytes(b"candidate")
        session = Session(self.root, original, candidate, self.root, 36,
                          {"sha256": digest(b"original")}, lambda _: None, None)
        declarations = {"permissions": [], "components": [{"type": "activity", "exported": "0xffffffff"}]}
        with patch("feature_agent.manifest_surface", return_value=declarations) as inspect:
            result = session.dispatch("inspect_manifest", {"artifact": "original"})
            self.assertEqual(result["sha256"], digest(b"original"))
            self.assertEqual(result["components"], declarations["components"])
            inspect.assert_called_once_with(original, self.root)
            with self.assertRaises(ValueError):
                session.dispatch("inspect_manifest", {"artifact": "candidate"})
            session.built(candidate, {"sha256": digest(b"candidate")})
            result = session.dispatch("inspect_manifest", {"artifact": "candidate"})
            self.assertEqual(result["artifact"], "candidate")
            self.assertEqual(result["sha256"], digest(b"candidate"))
            candidate.write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "哈希"):
                session.dispatch("inspect_manifest", {"artifact": "candidate"})
            self.assertEqual(inspect.call_count, 2)

    def test_new_process_bypasses_are_rejected_before_the_build_budget_is_spent(self):
        empty_policy = """
    new-instance v3, Landroid/os/StrictMode$VmPolicy$Builder;
    invoke-direct {v3}, Landroid/os/StrictMode$VmPolicy$Builder;-><init>()V
    invoke-virtual {v3}, Landroid/os/StrictMode$VmPolicy$Builder;->build()Landroid/os/StrictMode$VmPolicy;
    move-result-object v4
    .line 42
    invoke-static {v4}, Landroid/os/StrictMode;->setVmPolicy(Landroid/os/StrictMode$VmPolicy;)V
"""
        lax_policy = """
    sget-object p0, Landroid/os/StrictMode$VmPolicy;->LAX:Landroid/os/StrictMode$VmPolicy;
    invoke-static {p0}, Landroid/os/StrictMode;->setVmPolicy(Landroid/os/StrictMode$VmPolicy;)V
"""
        direct_bypass = "    invoke-static {}, Landroid/os/StrictMode;->disableDeathOnFileUriExposure()V"
        session = Session(self.root, Path("source.apk"), self.root / "candidate.apk",
                          Path("tools"), 36, {}, lambda _: None, None)
        name = "smali/example/Helper.smali"
        with patch.object(session, "start_build") as start:
            for source in (empty_policy, lax_policy, direct_bypass):
                with self.subTest(source=source):
                    session.workspace.create_text_file(name, source.replace("\n", "\r\n"))
                    with self.assertRaisesRegex(ValueError, "StrictMode"):
                        session.dispatch("build_apk", {})
                    session.workspace.restore_file(name)
            start.assert_not_called()
            self.assertEqual(session.build_count, 0)

    def test_existing_bypass_and_new_vm_policy_with_detection_reach_builder(self):
        name = "smali/example/Helper.smali"
        path = self.root / name
        path.parent.mkdir(parents=True)
        existing = "    invoke-static {}, Landroid/os/StrictMode;->disableDeathOnFileUriExposure()V\n"
        path.write_text(existing, encoding="utf-8")
        enforced_policy = """
    new-instance v0, Landroid/os/StrictMode$VmPolicy$Builder;
    invoke-direct {v0}, Landroid/os/StrictMode$VmPolicy$Builder;-><init>()V
    invoke-virtual {v0}, Landroid/os/StrictMode$VmPolicy$Builder;->detectFileUriExposure()Landroid/os/StrictMode$VmPolicy$Builder;
    invoke-virtual {v0}, Landroid/os/StrictMode$VmPolicy$Builder;->build()Landroid/os/StrictMode$VmPolicy;
    move-result-object v0
    invoke-static {v0}, Landroid/os/StrictMode;->setVmPolicy(Landroid/os/StrictMode$VmPolicy;)V
"""
        session = Session(self.root, Path("source.apk"), self.root / "candidate.apk",
                          Path("tools"), 36, {}, lambda _: None, None)
        row = session.workspace.read_text(name)
        session.workspace.replace_text(name, row["sha256"], existing, existing + enforced_policy)
        with patch.object(session, "start_build", side_effect=RuntimeError("builder reached")) as start:
            with self.assertRaisesRegex(RuntimeError, "builder reached"):
                session.dispatch("build_apk", {})
        start.assert_called_once_with()

    def test_original_can_be_reproduced_without_building_and_cannot_verify_a_candidate(self):
        source, candidate = self.root / "original.apk", self.root / "built.apk"
        source_sha, candidate_sha = "a" * 64, "b" * 64
        tester = Mock()
        tester.test.side_effect = lambda artifact, sha: {"installed_sha256": sha}
        tester.action.return_value = {"ui": {}}
        session = Session(self.root, source, self.root / "out.apk",
                          Path("tools"), 36, {"sha256": source_sha}, lambda _: None, tester)
        self.assertEqual(session.dispatch("test_original", {})["tested_input"], "original")
        tester.test.assert_called_once_with(source, source_sha)
        self.assertEqual(session.dispatch("device_action", {"action": "read"})["tested_input"], "original")
        self.assertEqual(session.build_count, 0)
        with self.assertRaises(ValueError):
            session.finish("candidate", "The original was observed")
        session.built(candidate, {"sha256": candidate_sha})
        self.assertIsNone(session.candidate_verification())
        self.assertEqual(session.dispatch("test_apk", {})["tested_input"], "candidate")
        self.assertEqual(session.candidate_verification()["installed_sha256"], candidate_sha)
        session.dispatch("test_original", {})
        self.assertIsNone(session.candidate_verification())

    def test_rejected_original_install_cannot_receive_device_actions(self):
        tester = Mock()
        tester.test.return_value = {"installed_sha256": None, "install": {"exit": 1}}
        session = Session(self.root, Path("source.apk"), self.root / "out.apk",
                          Path("tools"), 36, {"sha256": "a" * 64}, lambda _: None, tester)
        session.dispatch("test_original", {})
        with self.assertRaises(ValueError):
            session.dispatch("device_action", {"action": "read"})
        tester.action.assert_not_called()

    def test_platform_library_declaration_requires_device_evidence_and_preserves_manifest(self):
        manifest = self.root / "AndroidManifest.xml"
        original = (b'<manifest xmlns:android="http://schemas.android.com/apk/res/android">\r\n'
                    b'    <application><activity android:name=".Main"/>\r\n'
                    b'    </application>\r\n'
                    b'</manifest>\r\n')
        manifest.write_bytes(original)
        tester = Mock()
        tester.test.return_value = {"installed_sha256": "a" * 64}
        library = {"name": "android.test.mock", "evidence": "listed-by-target-package-manager"}
        tester.action.return_value = {"runtime_crash": {"platform_library": library}}
        session = Session(self.root, Path("original.apk"), self.root / "out.apk",
                          Path("tools"), 36, {"sha256": "a" * 64}, lambda _: None, tester)
        name = "declare_platform_library"
        args = {"name": library["name"]}
        with self.assertRaisesRegex(ValueError, "设备证据"):
            session.dispatch(name, args)
        session.dispatch("test_original", {})
        session.dispatch("device_action", {"action": "tap_text", "text": "Open"})
        with self.assertRaisesRegex(ValueError, "设备证据"):
            session.dispatch(name, {"name": "org.apache.http.legacy"})
        self.assertTrue(session.dispatch(name, args)["changed"])
        self.assertFalse(session.dispatch(name, args)["changed"])
        declaration = b'        <uses-library android:name="android.test.mock" android:required="false"/>\r\n'
        self.assertEqual(manifest.read_bytes().replace(declaration, b""), original)
        self.assertEqual([change["path"] for change in session.changes()], [manifest.name])
        self.assertEqual(session.build_count, 0)
        session.dispatch("restore_file", {"path": manifest.name})
        self.assertEqual(manifest.read_bytes(), original)

    def test_rehearsal_is_bound_to_the_current_build_and_kept_separate_from_final_verification(self):
        tester = Mock()
        steps = [{"action": "expect", "value": "Ready"}]
        session = Session(self.root, Path("original.apk"), self.root / "out.apk",
                          Path("tools"), 36, {}, lambda _: None, tester, steps)
        name = "rehearse_acceptance"
        for device_available, configured_steps, advertised in (
                (True, steps, True), (True, (), False), (False, steps, False)):
            names = {tool["function"]["name"] for tool in tool_definitions(device_available, configured_steps)}
            self.assertEqual(name in names, advertised)
        with self.assertRaises(ValueError):
            session.dispatch(name, {})

        first, second = self.root / "built1.apk", self.root / "built2.apk"
        failed = {"function": "failed", "failed_step": {"index": 1, "value": "Ready"}}
        passed = {"function": "passed"}
        with patch("feature_agent.rehearse_candidate", side_effect=[failed, passed]) as rehearse:
            session.built(first, {"sha256": "a" * 64})
            self.assertEqual(session.dispatch(name, {}), failed)
            self.assertEqual(session.dispatch(name, {}), failed)
            path = "AndroidManifest.xml"
            read = session.workspace.read_text(path)
            session.workspace.replace_text(path, read["sha256"], "<manifest/>", "<manifest />")
            with self.assertRaises(ValueError):
                session.dispatch(name, {})
            session.built(second, {"sha256": "b" * 64})
            self.assertEqual(session.dispatch(name, {}), passed)
            self.assertEqual([call.args for call in rehearse.call_args_list],
                             [(tester, first, "a" * 64, steps), (tester, second, "b" * 64, steps)])
        self.assertIsNone(session.candidate_verification())
        self.assertEqual(session.build_count, 0)

    def test_rollback_reuses_the_exact_previous_build_after_a_failed_attempt(self):
        session = Session(self.root, Path("source.apk"), self.root/"candidate.apk",
                          Path("tools"), 36, {}, lambda _: None, None)
        path = "AndroidManifest.xml"
        read = session.workspace.read_text(path)
        session.workspace.replace_text(path, read["sha256"], "<manifest/>", "<verified/>")
        with session.stack:
            artifact = session.start_build() / "verified.apk"
            artifact.write_bytes(b"signed")
            report = {"sha256": digest(b"signed")}
            session.built(artifact, report, reusable=True)
            read = session.workspace.read_text(path)
            session.workspace.replace_text(path, read["sha256"], "<verified/>", "<invalid/>")
            session.start_build()
            with self.assertRaises(ValueError):
                session.finish("candidate", "Edited content has never been built")
            read = session.workspace.read_text(path)
            session.workspace.replace_text(path, read["sha256"], "<invalid/>", "<verified/>")
            session.finish("candidate", "Restored exact file contents")
            self.assertEqual(session.artifact, artifact)
            self.assertEqual(session.build_count, 2)
            self.assertIsNone(session.device_report)

    def test_device_timeout_returns_to_model_and_invalidates_observation(self):
        tester = Mock()
        tester.test.return_value = {"installed_sha256": "source"}
        tester.action.side_effect = [TimeoutExpired(["adb", "shell"], 30), {"ui": {"nodes": []}}]
        session = Session(self.root, Path("source.apk"), self.root / "candidate.apk",
                          Path("tools"), 36, {"sha256": "source"}, lambda _: None, tester)
        steps = [
            ("test_original", {}),
            ("device_action", {"action": "cold_start"}),
            ("device_action", {"action": "read"}),
            ("test_original", {}),
            ("device_action", {"action": "read"}),
            ("finish", {"outcome": "analysis", "summary": "Recovered observation"}),
        ]
        responses = iter(enumerate(steps))

        def model(url, payload, key, **kwargs):
            index, (name, args) = next(responses)
            if index == 2:
                error = json.loads(payload["messages"][-1]["content"])
                self.assertFalse(error["ok"])
                self.assertIn("30", error["error"])
                self.assertNotIn("adb", error["error"])
            return {"content": None, "tool_calls": [{"id": f"call-{index}", "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)}}]}

        with patch("feature_agent._request", side_effect=model):
            _loop(session, [], [], {name for name, _ in steps},
                  {"url": "https://example.test", "model": "example", "key": ""}, lambda _: None)
        self.assertEqual([row["ok"] for row in session.trace], [True, False, False, True, True, True])
        self.assertEqual(tester.test.call_count, 2)
        self.assertEqual(tester.action.call_count, 2)

    def test_long_tool_history_preserves_recent_results_and_reasoning(self):
        session = Session(self.root, Path("source.apk"), self.root / "candidate.apk",
                          Path("tools"), 36, {}, lambda _: None, None)
        dispatch = session.dispatch
        evidence = {"text": "e" * 3000}
        requests = 0

        def model(url, payload, key, **kwargs):
            nonlocal requests
            index = requests
            requests += 1
            if index == 6:
                previous = [item for item in payload["messages"] if item["role"] == "assistant"]
                tools = [item for item in payload["messages"] if item["role"] == "tool"]
                self.assertEqual([item["reasoning_content"] for item in previous],
                                 [f"reasoning-{i}" for i in range(6)])
                self.assertEqual([item["tool_call_id"] for item in tools], [f"call-{i}" for i in range(6)])
                self.assertTrue(json.loads(tools[0]["content"])["history_compacted"])
                self.assertEqual(json.loads(tools[-1]["content"])["result"], evidence)
            name = "finish" if index == 6 else "read_text"
            args = {"outcome": "analysis", "summary": "Read evidence"} if name == "finish" else {}
            return {"content": None, "reasoning_content": f"reasoning-{index}",
                    "tool_calls": [{"id": f"call-{index}", "type": "function",
                                    "function": {"name": name, "arguments": json.dumps(args)}}]}

        with (patch("feature_agent.MAX_CONTEXT_CHARS", 16500),
              patch("feature_agent._request", side_effect=model),
              patch.object(session, "dispatch",
                           side_effect=lambda name, args: evidence if name == "read_text" else dispatch(name, args))):
            _loop(session, [], [], {"read_text", "finish"},
                  {"url": "https://api.deepseek.com", "model": "deepseek-flash", "key": ""}, lambda _: None)
        self.assertEqual(requests, 7)
        self.assertEqual(session.trace[0]["result"], evidence)

    def test_build_failure_is_returned_to_model_for_repair(self):
        def call(name, **args):
            return {"role": "assistant", "reasoning_content": "provider-continuation",
                    "tool_calls": [{"id": name+str(len(responses)), "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)}}]}

        responses = []
        steps = [
            ("read_text", {"path": "AndroidManifest.xml"}),
            ("replace_text", {"path": "AndroidManifest.xml", "sha256": digest(b"<manifest/>"),
                              "old": "<manifest/>", "new": "<bad/>"}),
            ("build_apk", {}),
            ("read_text", {"path": "AndroidManifest.xml"}),
            ("replace_text", {"path": "AndroidManifest.xml", "sha256": digest(b"<bad/>"),
                              "old": "<bad/>", "new": "<manifest />"}),
            ("build_apk", {}),
            ("finish", {"outcome": "candidate", "summary": "fixed after compile error"}),
        ]

        def model(url, payload, key, **kwargs):
            self.assertEqual(payload["thinking"], {"type": "enabled"})
            if len(responses) == 3:
                self.assertIn("compile failed", payload["messages"][-1]["content"])
                self.assertEqual(payload["messages"][-2]["reasoning_content"], "provider-continuation")
            name, args = steps[len(responses)]
            responses.append(name)
            return call(name, **args)

        build_calls = []
        def build(source, destination, api, tools, transform):
            build_calls.append(destination)
            if len(build_calls) == 1:
                raise RuntimeError("compile failed")
            destination.write_bytes(b"signed")
            return {"source_sha256": "source", "sha256": digest(b"signed")}

        facts = {"sha256": "source", "package": "example", "min_sdk": 2, "target_sdk": 24}
        with (patch("feature_agent.inspect_package", return_value=facts),
              patch("feature_agent.decoded_apk", return_value=nullcontext(self.root)),
              patch("feature_agent._request", side_effect=model),
              patch("feature_agent.build_modified", side_effect=build)):
            result = run(Path("input.apk"), self.root/"out/candidate.apk", Path("tools"), 36,
                         "source", "arbitrary requirement",
                         {"url": "https://api.deepseek.com", "model": "deepseek-flash", "key": ""},
                         lambda _: None)
        self.assertEqual(result["outcome"], "candidate")
        self.assertEqual((self.root/"out/candidate.apk").read_bytes(), b"signed")
        self.assertEqual(len(build_calls), 2)
        self.assertFalse(result["tool_trace"][2]["ok"])
        self.assertNotIn("provider-continuation", (self.root/"out/agent-trace.json").read_text(encoding="utf-8"))
