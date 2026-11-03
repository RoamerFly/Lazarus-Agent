import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch

from model_tools import ModelToolError, run_agent


EVIDENCE = {"E1_target_sdk": 27, "E2_device_api": 36, "E5_install": "SUCCESS", "E7_missing_class": "x"}


class Response:
    def __init__(self, message): self.message = message
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, _size): return json.dumps({"choices": [{"message": self.message}]}).encode()


class Opener:
    def __init__(self, replies): self.replies = iter(replies); self.requests = []
    def open(self, request, timeout): self.requests.append(request); return Response(next(self.replies))


class ModelToolsTests(unittest.TestCase):
    def test_local_compatible_service_round_trip(self):
        replies = iter([
            {"role": "assistant", "tool_calls": [{"id": "a", "function": {"name": "read_runtime_summary", "arguments": "{}"}}]},
            {"role": "assistant", "tool_calls": [{"id": "b", "function": {"name": "select_repair", "arguments": json.dumps({"action": "report-limit", "reason": "证据不足", "evidence_ids": ["E5_install"]})}}]},
        ])
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                body = json.dumps({"choices": [{"message": next(replies)}]}).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            def log_message(self, *_args): pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            result = run_agent(EVIDENCE, ["report-limit"], "report-limit",
                               {"url": "http://127.0.0.1:%d" % server.server_port, "model": "test"})
        finally:
            server.shutdown(); server.server_close(); thread.join()
        self.assertEqual(result["source"], "model-tools")

    def test_reads_tool_then_returns_allowed_selection(self):
        opener = Opener([
            {"role": "assistant", "tool_calls": [{"id": "a", "function": {"name": "read_manifest_summary", "arguments": "{}"}}]},
            {"role": "assistant", "tool_calls": [{"id": "b", "function": {"name": "select_repair", "arguments": json.dumps({"action": "try-platform-http-library", "reason": "缺少类", "evidence_ids": ["E1_target_sdk"]})}}]},
        ])
        with patch("model_tools.build_opener", return_value=opener):
            result = run_agent(EVIDENCE, ["try-platform-http-library", "report-limit"], "report-limit",
                               {"url": "http://127.0.0.1:8800", "model": "test"})
        self.assertEqual(result["action"], "try-platform-http-library")
        self.assertEqual(result["tool_trace"], ["read_manifest_summary", "select_repair"])
        second = json.loads(opener.requests[1].data)
        self.assertIn("E1_target_sdk", second["messages"][-1]["content"])

    def test_rejects_unknown_action_and_never_calls_inspector(self):
        opener = Opener([{"role": "assistant", "tool_calls": [{"id": "b", "function": {"name": "select_repair", "arguments": json.dumps({"action": "write-apk", "reason": "x", "evidence_ids": ["E5_install"]})}}]}])
        with patch("model_tools.build_opener", return_value=opener):
            with self.assertRaises(ModelToolError):
                run_agent(EVIDENCE, ["report-limit"], "report-limit", {"url": "http://localhost:8800", "model": "test"}, lambda: self.fail("unexpected"))

    def test_requires_evidence_to_have_been_read(self):
        opener = Opener([{"role": "assistant", "tool_calls": [{"id": "b", "function": {"name": "select_repair", "arguments": json.dumps({"action": "report-limit", "reason": "x", "evidence_ids": ["E5_install"]})}}]}])
        with patch("model_tools.build_opener", return_value=opener):
            with self.assertRaises(ModelToolError):
                run_agent(EVIDENCE, ["report-limit"], "report-limit", {"url": "http://localhost:8800", "model": "test"})

    def test_rejects_duplicate_call_id(self):
        calls = [{"id": "repeat", "function": {"name": "read_runtime_summary", "arguments": "{}"}},
                 {"id": "repeat", "function": {"name": "read_manifest_summary", "arguments": "{}"}}]
        opener = Opener([{"role": "assistant", "tool_calls": calls}])
        with patch("model_tools.build_opener", return_value=opener):
            with self.assertRaises(ModelToolError):
                run_agent(EVIDENCE, ["report-limit"], "report-limit", {"url": "http://localhost:8800", "model": "test"})

    def test_verified_shared_library_dynamically_enables_repair(self):
        opener = Opener([
            {"role": "assistant", "tool_calls": [{"id": "read", "function": {"name": "read_runtime_summary", "arguments": "{}"}}]},
            {"role": "assistant", "tool_calls": [{"id": "lookup", "function": {"name": "lookup_shared_library", "arguments": "{}"}}]},
            {"role": "assistant", "tool_calls": [{"id": "select", "function": {"name": "select_repair", "arguments": json.dumps({"action": "try-platform-shared-library", "reason": "系统库实际包含缺失类", "evidence_ids": ["E7_missing_class", "E8_platform_library"]})}}]},
        ])
        callback = Mock(return_value="org.apache.http.legacy")
        with patch("model_tools.build_opener", return_value=opener):
            result = run_agent(EVIDENCE, ["report-limit"], "report-limit", {"url": "http://localhost:8800", "model": "test"}, lookup_library=callback)
        self.assertEqual(result["verified_library"], "org.apache.http.legacy")
        callback.assert_called_once_with()
        self.assertIn("try-platform-shared-library", json.dumps(json.loads(opener.requests[2].data)["tools"], ensure_ascii=False))

    def test_shared_library_is_rejected_without_verified_lookup(self):
        opener = Opener([
            {"role": "assistant", "tool_calls": [{"id": "read", "function": {"name": "read_runtime_summary", "arguments": "{}"}}]},
            {"role": "assistant", "tool_calls": [{"id": "select", "function": {"name": "select_repair", "arguments": json.dumps({"action": "try-platform-shared-library", "reason": "x", "evidence_ids": ["E7_missing_class"]})}}]},
        ])
        with patch("model_tools.build_opener", return_value=opener):
            with self.assertRaises(ModelToolError):
                run_agent(EVIDENCE, ["report-limit", "try-platform-shared-library"], "report-limit", {"url": "http://localhost:8800", "model": "test"}, lookup_library=lambda: None)

    def test_shared_library_tool_is_unavailable_without_callback(self):
        opener = Opener([{"role": "assistant", "tool_calls": [{"id": "lookup", "function": {"name": "lookup_shared_library", "arguments": "{}"}}]}])
        with patch("model_tools.build_opener", return_value=opener):
            with self.assertRaises(ModelToolError):
                run_agent(EVIDENCE, ["report-limit"], "report-limit", {"url": "http://localhost:8800", "model": "test"})

    def test_inspector_is_zero_argument_and_result_is_given_to_model(self):
        opener = Opener([
            {"role": "assistant", "tool_calls": [{"id": "a", "function": {"name": "inspect_apk_structure", "arguments": "{}"}}]},
            {"role": "assistant", "tool_calls": [{"id": "runtime", "function": {"name": "read_runtime_summary", "arguments": "{}"}}]},
            {"role": "assistant", "tool_calls": [{"id": "b", "function": {"name": "select_repair", "arguments": json.dumps({"action": "report-limit", "reason": "x", "evidence_ids": ["E5_install"]})}}]},
        ])
        with patch("model_tools.build_opener", return_value=opener):
            result = run_agent(EVIDENCE, ["report-limit"], "report-limit", {"url": "https://example.test", "model": "test"}, lambda: {"native_abis": ["armeabi-v7a"], "raw_log": "secret"})
        self.assertEqual(result["tool_trace"][0], "inspect_apk_structure")
        inspection_message = json.loads(opener.requests[1].data)["messages"][-1]["content"]
        self.assertIn("armeabi-v7a", inspection_message)
        self.assertNotIn("secret", inspection_message)

    def test_refuses_nonlocal_http(self):
        with self.assertRaises(ModelToolError):
            run_agent(EVIDENCE, ["report-limit"], "report-limit", {"url": "http://example.com", "model": "test"})
