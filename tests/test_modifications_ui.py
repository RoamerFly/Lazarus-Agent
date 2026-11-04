"""Static DOM contract for the browser-only modification workbench controls."""
from html.parser import HTMLParser
from pathlib import Path
import subprocess
import unittest


ROOT=Path(__file__).resolve().parents[1]


class _Ids(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids=set()
    def handle_starttag(self, tag, attrs):
        values=dict(attrs)
        if values.get("id"):
            self.ids.add(values["id"])


class ModificationUiTests(unittest.TestCase):
    def test_page_has_all_controls_referenced_by_the_modification_script(self):
        parser=_Ids()
        parser.feed((ROOT/"web/index.html").read_text(encoding="utf-8"))
        required={
            "modificationKind","modificationUseModel","modificationRequest","modificationBaseVersion",
            "modificationPlan","modificationRun","modificationRevise","modificationStatus",
            "rememberCriteria","localizeCriteria","localizeRows","addLocalizationRow",
            "browseLocalizationResources","localizeResourceList","rememberFirstTap","rememberFirstExpect",
            "rememberSecondTap","rememberSecondExpect",
        }
        self.assertTrue(required <= parser.ids, required-parser.ids)

    def test_studio_controls_survive_moving_sections_to_dialogs(self):
        import re
        parser=_Ids()
        parser.feed((ROOT/"web/index.html").read_text(encoding="utf-8"))
        script=(ROOT/"web/studio.js").read_text(encoding="utf-8")
        required=set(re.findall(r'el\("([^"\n]+)"\)',script))
        self.assertTrue(required <= parser.ids, required-parser.ids)

    def test_modification_script_is_loaded_and_syntax_checked(self):
        page=(ROOT/"web/index.html").read_text(encoding="utf-8")
        self.assertIn('src="/modifications.js"',page)
        script=ROOT/"web/modifications.js"
        result=subprocess.run(["node","--check",str(script)],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_browser_contract_mentions_resource_and_plan_routes(self):
        script=(ROOT/"web/modifications.js").read_text(encoding="utf-8")
        self.assertIn('/modify/resources',script)
        self.assertIn('/modify/plan',script)
        self.assertIn('/modify/run',script)
        self.assertIn('use_model',script)
        self.assertIn('至少勾选一条译文作为界面验收项',script)

