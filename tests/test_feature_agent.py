"""Regression checks for generic editing, failed builds, and evidence gates."""
from contextlib import nullcontext
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from agent_workspace import Workspace, digest
from feature_agent import Session, run


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
