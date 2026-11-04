"""Regression checks for chat routing, format detection and untrusted model translations."""

import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from auto_translation import is_command, translations
from auto_localize import transform
from unity_localization import bitmap_font, groups, table


class ChatLocalizationTests(unittest.TestCase):
    def test_imperatives_are_separate_from_feedback(self):
        for t in ("汉化成中文", "请帮我把这个游戏翻译成简体中文", "把APK汉化一下"):
            self.assertTrue(is_command(t))
        for t in ("上次汉化成中文后闪退", "怎么汉化成中文？", "不要汉化成中文", "能汉化吗", None):
            self.assertFalse(is_command(t))

    def test_model_preserves_tokens_and_excludes_urls(self):
        config = {"enabled": True, "url": "http://localhost:8000", "model": "test", "key": ""}

        def answer(req, timeout):
            rows = json.loads(json.loads(req.data)["messages"][1]["content"])
            self.assertEqual(rows, ["Flight mode", "Go to {0}"])
            return io.BytesIO(
                json.dumps(
                    {"choices": [{"message": {"content": json.dumps(["飞行模式", "前往"])}}]}
                ).encode()
            )

        with (
            patch("auto_translation.get_config", return_value=config),
            patch("auto_translation.safe_urlopen", side_effect=answer),
        ):
            result, count = translations(
                ["open", "Flight mode", "Go to {0}", "https://example.com"], True, lambda _: None
            )
        self.assertEqual(result, {"open": "打开", "Flight mode": "飞行模式"})
        self.assertEqual(count, 1)

    def test_ordinary_resources_without_game_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            decoded = Path(tmp)
            values = decoded / "res/values"
            values.mkdir(parents=True)
            (values / "ui.xml").write_text(
                '<resources><string name="arbitrary_id">Save</string></resources>', encoding="utf8"
            )
            stats = transform(decoded, False, lambda _: None)
            self.assertEqual(stats["translated"], 1)
            self.assertIn("保存", (decoded / "res/values-zh-rCN/strings.xml").read_text(encoding="utf8"))

    def test_discovers_groups_language_and_material_by_structure(self):
        base = "assets/bin/Data/sharedassets73.assets"
        self.assertEqual(
            list(groups([base + ".split1", base + ".split0"])),
            [(base, [base + ".split0", base + ".split1"])],
        )
        with self.assertRaises(ValueError):
            list(groups([base + ".split2"]))
        text = b"unfamiliar=Back\r\nother=Start"
        blob = struct.pack("<I", 2) + b"en\0\0" + struct.pack("<I", len(text)) + text
        self.assertEqual(table(SimpleNamespace(get_raw_data=lambda: blob))[2], text.decode())
        raw = bytearray(76)
        struct.pack_into("<I", raw, 28, 947)
        struct.pack_into("<5I", raw, 48, 48, 32, 256, 256, 4)
        raw[68:72] = b"Demo"
        chars = "0123456789AZaz"
        struct.pack_into("<I", raw, 72, len(chars))
        for c in chars:
            raw.extend(struct.pack("<10i", ord(c), 1, 1, 9, 12, 0, 0, 12, 15, 0))
        raw.extend(bytes(52))
        ptr = SimpleNamespace(m_Texture=SimpleNamespace(m_FileID=0, m_PathID=771))
        mat = SimpleNamespace(
            type=SimpleNamespace(name="Material"),
            read=lambda: SimpleNamespace(
                m_SavedProperties=SimpleNamespace(m_TexEnvs=[(SimpleNamespace(name="_MainTex"), ptr)])
            ),
        )
        spec = bitmap_font(
            SimpleNamespace(objects={947: mat, 771: object()}), SimpleNamespace(get_raw_data=lambda: raw)
        )
        self.assertEqual(spec[-1], 771)
        self.assertEqual(spec[2:5], (256, 256, 48))

    def test_chat_uses_current_agent_configuration_and_keeps_legacy_flags(self):
        import workbench
        body={"use_configured_model":True,"local_translate":True}
        with patch.object(workbench,"public_config",return_value={"enabled":True,"url":"https://unit.test/v1","model":"unit"}):
            self.assertIs(workbench.chat_model_mode(body),True)
        with patch.object(workbench,"public_config",return_value={"enabled":False,"url":"","model":""}):
            self.assertEqual(workbench.chat_model_mode(body),"local")
        self.assertIs(workbench.chat_model_mode({"translate_with_model":True}),True)
        self.assertIs(workbench.chat_model_mode({}),False)

    def test_chat_endpoint_runs_worker_and_serves_only_published_candidate(self):
        import time
        from fastapi.testclient import TestClient
        import workbench

        def fake_build(source, destination, api, tools, use_model, progress):
            self.assertEqual(source.read_bytes(), b"original")
            self.assertFalse(use_model)
            progress("检查测试资源")
            destination.write_bytes(b"signed-test-output")
            return {"transform": {"translated": 2, "discovered": 4}}

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(workbench, "STORAGE", Path(tmp)),
            patch("auto_localize.build", side_effect=fake_build),
        ):
            job = "a" * 32
            folder = Path(tmp) / job
            folder.mkdir()
            (folder / "original.apk").write_bytes(b"original")
            workbench.save(
                folder,
                {
                    "id": job,
                    "api": 36,
                    "status": "ready",
                    "events": [],
                    "package": {"package": "example.other"},
                },
            )
            client = TestClient(workbench.app, base_url=f"http://127.0.0.1:{workbench.PORT}")
            url = "/api/jobs/" + job
            result = client.post(
                url + "/message",
                headers={"x-reviver-token": workbench.TOKEN},
                json={"kind": "issue", "text": "汉化成中文"},
            )
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()["status"], "running")
            for _ in range(100):
                state = client.get(url).json()
                if state["status"] != "running":
                    break
                time.sleep(0.01)
            run_id = state["messages"][-1]["localization_id"]
            self.assertEqual(client.get(url + "/localized/" + run_id).content, b"signed-test-output")
            self.assertEqual(client.get(url + "/localized/" + "0" * 32).status_code, 404)
            self.assertFalse(state.get("export"))
