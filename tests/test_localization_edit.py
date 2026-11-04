import tempfile
from pathlib import Path
import unittest

from localization_edit import LocalizationError, apply, catalog, inspect


DEFAULT = '''<?xml version="1.0" encoding="utf-8"?>
<resources>
    <string name="open_record">Open record</string>
    <string name="count">Opened %1$d files</string>
    <string name="brand" translatable="false">Lazarus</string>
</resources>
'''


def criteria(translations, resources=None, checks=None):
    first, value = next(iter(translations.items()))
    return {"translations": translations, "resources": resources or {},
            "checks": checks or [{"original": first, "translated": value}]}


class LocalizationEditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        base = self.root / "res" / "values"
        base.mkdir(parents=True)
        (base / "strings.xml").write_text(DEFAULT, encoding="utf-8")
        self.addCleanup(self.temp.cleanup)

    def test_catalog_inspect_and_apply_creates_zh_cn_resource(self):
        rows = catalog(self.root)
        self.assertIn({"name": "open_record", "original": "Open record", "translated": ""}, rows)
        found = inspect(self.root, criteria({"Open record": "打开记录", "Opened %1$d files": "已打开 %1$d 个文件"},
                                           checks=[{"original": "Open record", "translated": "打开记录"}]))
        self.assertTrue(found["available"])
        result = apply(self.root, found["plan"])
        self.assertTrue(result["applied"])
        self.assertEqual(result["audit"]["kind"], "add-zh-cn-resources-v1")
        text = (self.root / "res/values-zh-rCN/strings.xml").read_text(encoding="utf-8")
        self.assertIn('name="open_record">打开记录', text)
        self.assertIn('name="count">已打开 %1$d 个文件', text)

    def test_rejects_placeholder_mismatch_without_write(self):
        with self.assertRaisesRegex(LocalizationError, "占位符"):
            inspect(self.root, criteria({"Opened %1$d files": "已打开文件"}))
        self.assertFalse((self.root / "res/values-zh-rCN").exists())

    def test_duplicate_original_requires_resource_name(self):
        path = self.root / "res/values/other.xml"
        path.write_text('<resources><string name="open_again">Open record</string></resources>', encoding="utf-8")
        with self.assertRaisesRegex(LocalizationError, "唯一定位"):
            inspect(self.root, criteria({"Open record": "打开记录"}))
        found = inspect(self.root, criteria({"Open record": "打开记录"}, {"Open record": "open_again"}))
        self.assertEqual(found["plan"]["edits"][0]["name"], "open_again")

    def test_existing_zh_cn_in_another_xml_is_updated_without_duplicate(self):
        target = self.root / "res/values-zh-rCN/labels.xml"
        target.parent.mkdir()
        target.write_text('<resources><string name="open_record">旧译文</string></resources>', encoding="utf-8")
        found = inspect(self.root, criteria({"Open record": "打开记录"}))
        self.assertEqual(found["plan"]["edits"][0]["target_file"], "res/values-zh-rCN/labels.xml")
        apply(self.root, found["plan"])
        self.assertEqual(ET_value(target, "open_record"), "打开记录")
        self.assertFalse((self.root / "res/values-zh-rCN/strings.xml").exists())

    def test_traditional_chinese_neither_blocks_nor_supplies_zh_cn_value(self):
        target = self.root / "res/values-zh-rTW/strings.xml"
        target.parent.mkdir()
        target.write_text('<resources><string name="open_record">開啟記錄</string></resources>', encoding="utf-8")
        row = next(item for item in catalog(self.root) if item["name"] == "open_record")
        self.assertEqual(row["translated"], "")
        apply(self.root, inspect(self.root, criteria({"Open record": "打开记录"}))["plan"])
        self.assertEqual(ET_value(target, "open_record"), "開啟記錄")
        self.assertEqual(ET_value(self.root / "res/values-zh-rCN/strings.xml", "open_record"), "打开记录")

    def test_nontranslatable_and_tampered_plan_never_write(self):
        with self.assertRaisesRegex(LocalizationError, "不可翻译"):
            inspect(self.root, criteria({"Lazarus": "拉撒路"}))
        plan = inspect(self.root, criteria({"Open record": "打开记录"}))["plan"]
        plan["edits"][0]["name"] = "brand"
        with self.assertRaisesRegex(LocalizationError, "不一致"):
            apply(self.root, plan)
        self.assertFalse((self.root / "res/values-zh-rCN").exists())

    def test_android_escaping_preserves_quotes_backslash_prefix_and_percent(self):
        source = self.root / "res/values/special.xml"
        source.write_text('''<resources>
            <string name="literal">Don\\'t say \\"hello\\" from \\\\path: 100%%</string>
            <string name="plain_percent" formatted="false">100%</string>
        </resources>''', encoding="utf-8")
        rows = {item["name"]: item["original"] for item in catalog(self.root)}
        self.assertEqual(rows["literal"], 'Don\'t say "hello" from \\path: 100%')
        found = inspect(self.root, criteria({
            'Don\'t say "hello" from \\path: 100%': '@不要说 "你好"，路径 \\tmp：100%',
        }))
        apply(self.root, found["plan"])
        raw = ET_value(self.root / "res/values-zh-rCN/strings.xml", "literal")
        self.assertEqual(raw, '\\@不要说 \\"你好\\"，路径 \\\\tmp：100%%')
        plain = inspect(self.root, criteria({"100%": "?完成 100%"}, {"100%": "plain_percent"}))
        apply(self.root, plain["plan"])
        self.assertEqual(ET_value(self.root / "res/values-zh-rCN/strings.xml", "plain_percent"), "\\?完成 100%")

    def test_catalog_omits_aliases_nontranslatable_and_ambiguous_escapes(self):
        path = self.root / "res/values/unsafe.xml"
        path.write_text('''<resources>
            <string name="alias">@string/open_record</string>
            <string name="theme_alias">?attr/title</string>
            <string name="closed" translatable="false">Internal</string>
            <string name="odd">bad\\q</string>
            <item type="string" name="item_alias">@string/open_record</item>
        </resources>''', encoding="utf-8")
        names = {row["name"] for row in catalog(self.root)}
        self.assertFalse({"alias", "theme_alias", "closed", "odd", "item_alias"} & names)

    def test_existing_zh_cn_alias_is_not_overwritten_or_duplicated(self):
        target = self.root / "res/values-zh-rCN/aliases.xml"
        target.parent.mkdir()
        target.write_text('<resources><string name="open_record">@string/other</string></resources>', encoding="utf-8")
        with self.assertRaisesRegex(LocalizationError, "不是可安全编辑"):
            inspect(self.root, criteria({"Open record": "打开记录"}))

    def test_placeholder_rules_allow_indexed_reordering_but_not_unordered_reordering(self):
        path = self.root / "res/values/formats.xml"
        path.write_text('''<resources>
            <string name="indexed">%1$s has %2$d%%</string>
            <string name="ordered">%s has %d</string>
        </resources>''', encoding="utf-8")
        indexed = inspect(self.root, criteria({"%1$s has %2$d%": "已处理 %2$d 个 %1$s%"}))
        apply(self.root, indexed["plan"])
        self.assertEqual(ET_value(self.root / "res/values-zh-rCN/strings.xml", "indexed"), "已处理 %2$d 个 %1$s%%")
        with self.assertRaisesRegex(LocalizationError, "占位符"):
            inspect(self.root, criteria({"%s has %d": "%d 个 %s"}))

    def test_catalog_caps_simple_resources(self):
        lines = [f'<string name="s{i}">v{i}</string>' for i in range(1600)]
        (self.root / "res/values/many.xml").write_text("<resources>" + "".join(lines) + "</resources>", encoding="utf-8")
        self.assertLessEqual(len(catalog(self.root)), 1500)


def ET_value(path, name):
    import xml.etree.ElementTree as ET
    return next(element.text for element in ET.parse(path).getroot().findall("string") if element.get("name") == name)
