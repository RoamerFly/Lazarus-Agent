import base64
from contextlib import nullcontext
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from model_feedback import user_content, text_context_size, validate_vision_model, lookup_error

IMAGE = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + bytes(190000)).decode()
CONFIG = {"url": "https://api.deepseek.com", "model": "deepseek-flash", "key": "unit-only", "enabled": True}


class RuntimeFeedbackTests(unittest.TestCase):
    def test_image_is_sent_as_image_and_does_not_exhaust_text_budget(self):
        content = user_content("runtime fault", IMAGE, CONFIG)
        self.assertEqual(content[1]["type"], "image_url")
        self.assertEqual(content[1]["image_url"]["url"], IMAGE)
        self.assertLess(text_context_size([{"role": "user", "content": content}]), 200)
        with self.assertRaisesRegex(ValueError, "图像"):
            validate_vision_model({**CONFIG, "model": "deepseek-v4-pro"})

    def test_web_lookup_is_separate_from_editing_tools(self):
        with patch("model_tools._request", return_value={"content": "See https://developer.android.com/"}) as request:
            result = lookup_error(CONFIG, "Android SecurityException on start")
        self.assertIn("developer.android.com", result["summary"])
        self.assertEqual(request.call_args.args[1]["tools"], [{"type": "deepseek_web_search"}])
        with self.assertRaises(ValueError):
            lookup_error(CONFIG, r"E:\private\error.txt")

    def test_image_feedback_enters_agent_with_current_apk_and_can_be_reused(self):
        from fastapi.testclient import TestClient
        import workbench
        invocations = []
        def agent(source, output, tools, api, expected_hash, request, config, progress, **kwargs):
            invocations.append(kwargs)
            self.assertEqual(source.read_bytes(), b"original")
            return {"outcome": "analysis", "summary": "已识别运行中的错误提示"}
        with tempfile.TemporaryDirectory() as temporary, patch.object(workbench, "STORAGE", Path(temporary)), \
                patch("model_settings.get_config", return_value=CONFIG), patch("feature_agent.run", side_effect=agent), \
                patch.object(workbench.preview, "evidence", return_value={"log": "app only", "scope": "package-pids-only"}), \
                patch.object(workbench, "ready", return_value=False):
            job = "c" * 32
            folder = Path(temporary) / job
            folder.mkdir(); (folder / "original.apk").write_bytes(b"original")
            workbench.save(folder, {"id": job, "api": 36, "status": "ready", "events": [], "tests": [],
                                   "package": {"package": "sample.app", "sha256": "hash"}})
            client = TestClient(workbench.app, base_url=f"http://127.0.0.1:{workbench.PORT}")
            route = "/api/jobs/" + job
            headers = {"x-reviver-token": workbench.TOKEN}
            first = {"kind": "issue", "text": "启动成功，保存时偶发报错，请修复", "attachment": IMAGE,
                     "use_configured_model": True, "include_runtime_context": True}
            self.assertEqual(client.post(route + "/message", json=first, headers=headers).status_code, 200)
            for _ in range(100):
                state = client.get(route).json()
                if state["status"] != "running": break
                time.sleep(.01)
            self.assertEqual(invocations[0]["feedback_image"], IMAGE)
            self.assertEqual(invocations[0]["runtime_context"]["scope"], "package-pids-only")
            self.assertTrue(next(m for m in state["messages"] if m["role"] == "user")["attachment"])
            self.assertEqual(client.post(route + "/message", json={"kind": "issue", "text": "继续分析", "reuse_feedback": True,
                                  "use_configured_model": True}, headers=headers).status_code, 200)
            for _ in range(100):
                state = client.get(route).json()
                if state["status"] != "running": break
                time.sleep(.01)
            self.assertEqual(len(invocations), 2)
            self.assertEqual(invocations[1]["feedback_image"], IMAGE)
            self.assertNotIn("unit-only", (folder / "state.json").read_text(encoding="utf-8"))
