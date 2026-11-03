import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import model_settings
from storage_paths import StoragePaths


class ModelSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.paths = StoragePaths(Path(self.temp.name)).ensure()
        self.patch = patch("model_settings.resolve_storage_paths", return_value=self.paths)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.addCleanup(self.temp.cleanup)

    def test_url_rejects_remote_http_and_credentials(self):
        with self.assertRaises(model_settings.ModelSettingsError): model_settings.validate_url("http://example.test")
        with self.assertRaises(model_settings.ModelSettingsError): model_settings.validate_url("https://token@example.test")
        self.assertEqual(model_settings.validate_url("https://example.test/v1/"), "https://example.test/v1")

    def test_environment_configuration_never_needs_saved_key(self):
        result = model_settings.get_config({"REVIVER_MODEL_URL":"http://127.0.0.1:9000", "REVIVER_MODEL":"local", "REVIVER_MODEL_KEY":"abc"})
        self.assertEqual(result["key"], "abc")
        self.assertTrue(result["enabled"])

    @unittest.skipUnless(os.name=="nt", "Windows DPAPI integration")
    def test_blank_form_key_preserves_secret_only_for_same_profile(self):
        profile={"url":"https://example.test/v1","model":"unit-only","enabled":True}
        with patch.dict(os.environ,{"REVIVER_MODEL_URL":"","REVIVER_MODEL":""}):
            model_settings.save_config({**profile,"key":"test-only-sentinel"})
            self.assertEqual(model_settings.preview_config({**profile,"key":""})["key"],"test-only-sentinel")
            self.assertTrue(model_settings.save_config({**profile,"key":""})["has_key"])
            self.assertNotIn("test-only-sentinel",(self.paths.config/model_settings.CONFIG_NAME).read_text())
            self.assertFalse(model_settings.preview_config({**profile,"url":"https://new.test","key":""})["key"])
            self.assertFalse(model_settings.save_config({**profile,"key":"","clear_key":True})["has_key"])

    def test_standalone_environment_key_is_not_attached_to_saved_endpoint(self):
        path = self.paths.config / model_settings.CONFIG_NAME
        path.write_text(json.dumps({"schema":1,"url":"https://saved.test","model":"m","enabled":True}), encoding="utf-8")
        result = model_settings.get_config({"REVIVER_MODEL_KEY":"foreign"})
        self.assertEqual(result["url"], "https://saved.test")
        self.assertEqual(result["key"], "")

    def test_disabled_config_retains_details_but_marks_automatic_use_off(self):
        model_settings.save_config({"url":"https://example.test","model":"x","enabled":False})
        result = model_settings.get_config({})
        self.assertFalse(result["enabled"])
        self.assertEqual(result["url"], "https://example.test")

    def test_explicit_clear_removes_key(self):
        path = self.paths.config / model_settings.CONFIG_NAME
        path.write_text(json.dumps({"schema":1,"url":"https://old.test","model":"x","protected_key":"not-a-key"}), encoding="utf-8")
        model_settings.save_config({"enabled":False,"clear_key":True})
        self.assertNotIn("protected_key", json.loads(path.read_text(encoding="utf-8")))

    def test_preview_uses_new_key_without_writing_it(self):
        path = self.paths.config / model_settings.CONFIG_NAME
        preview = model_settings.preview_config({"url":"https://preview.test","model":"m","key":"temporary"})
        self.assertEqual(preview["key"], "temporary")
        self.assertFalse(path.exists())

    def test_non_windows_save_never_writes_key(self):
        with patch.object(model_settings.os, "name", "posix"):
            with self.assertRaises(model_settings.ModelSettingsError):
                model_settings.save_config({"url":"https://example.test", "model":"x", "key":"secret"})
            public = model_settings.save_config({"url":"https://example.test", "model":"x"})
        self.assertFalse(public["has_key"])
        self.assertNotIn("secret", (self.paths.config / model_settings.CONFIG_NAME).read_text(encoding="utf-8"))

    def test_changed_endpoint_drops_persisted_credential(self):
        existing = {"schema":1,"url":"https://old.test","model":"x","protected_key":"not-a-key"}
        path = self.paths.config / model_settings.CONFIG_NAME
        path.write_text(json.dumps(existing), encoding="utf-8")
        with patch.object(model_settings.os, "name", "nt"):
            with patch("model_settings._crypt", return_value=b"protected"):
                model_settings.save_config({"url":"https://new.test", "model":"x"})
        self.assertNotIn("protected_key", json.loads(path.read_text(encoding="utf-8")))

    def test_test_connection_requires_named_tool_call(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, _): return json.dumps({"choices":[{"message":{"tool_calls":[{"function":{"name":"inspect_repair_evidence"}}]}}]}).encode()
        class Opener:
            def open(self, request, timeout):
                self.request = request
                return Response()
        opener = Opener()
        with patch("model_settings.build_opener", return_value=opener):
            result=model_settings.test_connection({"url":"http://127.0.0.1:9000","model":"local","key":""})
        self.assertTrue(result["function_calling"])
        body=json.loads(opener.request.data)
        self.assertEqual(body["tool_choice"]["function"]["name"], "inspect_repair_evidence")
