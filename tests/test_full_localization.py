import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from auto_translation import translations, valid_target
from embedded_locale import reuse
from texture_localize import accepted, repaint


class FullLocalizationTests(unittest.TestCase):
    def test_external_model_reaches_last_batch_and_keeps_grouping_flag(self):
        values = [f"Test option {i} costs %1$,d" for i in range(405)]

        def reply(request, timeout):
            items = json.loads(json.loads(request.data)["messages"][1]["content"])
            translated = ["测试选项 %1$,d" for item in items]
            return io.BytesIO(
                json.dumps(
                    {"choices": [{"message": {"content": json.dumps(translated)}}]}
                ).encode()
            )

        cfg = dict(url="http://localhost:2", model="test", key="", enabled=True)
        with (
            patch("auto_translation.get_config", return_value=cfg),
            patch("auto_translation.safe_urlopen", side_effect=reply),
        ):
            result, n = translations(values, True, lambda _: None)
        self.assertEqual(n, 405)
        self.assertIn(values[-1], result)
        self.assertFalse(valid_target(values[-1], "测试选项 %1$d"))

    def test_compiled_locale_row_uses_named_slots_and_preserves_register_after_call(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            smali = Path(tmp) / "smali"
            smali.mkdir()
            (smali / "Row.smali").write_text(
                '.class public LRow;\n.method public constructor <init>(Ljava/lang/String;Ljava/lang/String;)V\n    .param p1, "zh_cn"\n    .param p2, "en_us"\n.end method\n',
                encoding="utf8",
            )
            code = (
                '    new-instance v2, LRow;\n\n    const-string v3, "矿山"\n'
                '    const-string v4, "Mine"\n\n    invoke-direct/range {v2 .. v4}, LRow;-><init>(Ljava/lang/String;Ljava/lang/String;)V\n'
            )
            data = smali / "Data.smali"
            data.write_text(code + code.replace("LRow;", "LUnknown;"), encoding="utf8")
            terms, n = reuse(smali.parent)
            self.assertEqual((terms, n), ({"Mine": "矿山"}, 1))
            output = data.read_text(encoding="utf8")
            self.assertIn('    const-string/jumbo v4, "Mine"\n', output)
            self.assertTrue(output.endswith(code.replace("LRow;", "LUnknown;")))

    def test_low_confidence_art_is_ignored_and_changes_are_local(self):
        path = Path("arbitrary.png")
        labels = accepted(
            {path: [("Play", 0.76, (10, 8, 40, 24)), ("Unclear", 0.61, (0, 0, 5, 5))]}
        )
        self.assertEqual(labels[path], [("Play", (10, 8, 40, 24))])
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / path
            original = Image.new("RGBA", (80, 50), "#888888")
            original.save(target)
            # The test runner may not have a Chinese font installed.
            try:
                repaint(target, [((10, 8, 40, 24), "开始")])
            except ValueError as exc:
                if "字体" in str(exc):
                    self.skipTest(str(exc))
                raise
            with Image.open(target) as updated:
                self.assertEqual(original.size, updated.size)
                self.assertEqual(
                    original.crop((45, 0, 80, 50)).tobytes(),
                    updated.crop((45, 0, 80, 50)).tobytes(),
                )
                self.assertNotEqual(original.tobytes(), updated.tobytes())
