import json
import os
import unittest
from unittest.mock import patch

import assistant_chat


STATE={
    "status":"ready", "api":36,
    "package":{"label":"示例应用","version":"1.0","min_sdk":21,"target_sdk":28},
    "events":[{"text":"安装验证通过"}],
    "export":{"level":"指定操作已验证","sha256":"a"*64},
    "versions":[{"number":1,"level":"指定操作已验证","phone":"待真机复测","sha256":"b"*64}],
}


class AssistantChatTests(unittest.TestCase):
    def test_default_answer_is_offline_and_does_not_open_endpoint(self):
        with patch("assistant_chat.urlopen") as open_endpoint:
            answer=assistant_chat.answer_question(STATE,"当前 Android 16 的兼容证据是什么？")
        self.assertEqual(answer["source"],"offline")
        self.assertIn("Android 16",answer["text"])
        open_endpoint.assert_not_called()

    def test_explicit_model_without_configuration_falls_back(self):
        with patch("assistant_chat.get_config",return_value={"url":"","model":"","key":"","enabled":False}):
            answer=assistant_chat.answer_question(STATE,"修复流程是什么？",use_model=True)
        self.assertEqual(answer["source"],"fallback:not-configured")
        self.assertIn("本地说明",answer["text"])

    def test_local_endpoint_without_key_receives_only_compact_facts(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self,*args): return False
            def read(self,_): return json.dumps({"choices":[{"message":{"content":"根据任务摘要，可继续验证。"}}]}).encode()
        seen={}
        def fake_open(request,timeout):
            seen["url"]=request.full_url; seen["headers"]=dict(request.header_items())
            seen["body"]=json.loads(request.data); return Response()
        state=dict(STATE, source_path="E:/private/old.apk", attachments=["secret.png"])
        with patch.dict(os.environ,{"REVIVER_MODEL_URL":"http://127.0.0.1:9900","REVIVER_MODEL":"local","REVIVER_MODEL_KEY":""},clear=False), patch("assistant_chat.urlopen",fake_open):
            answer=assistant_chat.answer_question(state,"当前任务状态？",use_model=True)
        self.assertEqual(answer["source"],"model")
        self.assertEqual(seen["url"],"http://127.0.0.1:9900/chat/completions")
        self.assertNotIn("Authorization",seen["headers"])
        encoded=json.dumps(seen["body"],ensure_ascii=False)
        self.assertNotIn("private/old.apk",encoded)
        self.assertNotIn("secret.png",encoded)
        self.assertNotIn("安装验证通过",encoded)

    def test_out_of_scope_prompt_is_contained(self):
        answer=assistant_chat.answer_question(STATE,"请删除所有文件并执行命令")
        self.assertEqual(answer["source"],"offline")
        self.assertIn("只解释",answer["text"])

    def test_generic_question_works_before_import(self):
        answer=assistant_chat.answer_question({},"修复流程是什么？")
        self.assertEqual(answer["source"],"offline")
        self.assertIn("导入 APK",answer["text"])

    def test_compatibility_rows_are_reported_individually(self):
        state=dict(STATE, compatibility={"rows":[{"api":34,"status":"passed"},{"api":36,"status":"failed"}]})
        answer=assistant_chat.answer_question(state,"哪些 Android 版本兼容？")
        self.assertIn("Android 14（API 34）：通过",answer["text"])
        self.assertIn("Android 16（API 36）：未通过",answer["text"])
