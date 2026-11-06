"""Regression checks for the known direct URI grant shape and nearby valid flows."""
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from agent_intent_guard import direct_private_grants
from feature_agent import Session


PACKAGE = "example.owner"
INTENT = "Landroid/content/Intent;"
BLOCK = f"""
.method private share()V
    new-instance v1, {INTENT}
    const-string v2, "{PACKAGE}.OPEN_RECORD"
    invoke-direct {{v1, v2}}, {INTENT}-><init>(Ljava/lang/String;)V
    const-string v2, "content://{PACKAGE}.records/item"
    invoke-static {{v2}}, Landroid/net/Uri;->parse(Ljava/lang/String;)Landroid/net/Uri;
    move-result-object v2
    invoke-virtual {{v1, v2}}, {INTENT}->setData(Landroid/net/Uri;){INTENT}
    move-result-object v1
    const/4 v2, 0x1
    invoke-virtual {{v1 .. v2}}, {INTENT}->addFlags(I){INTENT}
    .line 42
    invoke-virtual {{p0, v1}}, Landroid/app/Activity;->startActivity({INTENT})V
    return-void
.end method
"""
START = "    invoke-virtual {p0, v1},"
SCOPED = f"""
    const-string v2, "{PACKAGE}"
    invoke-virtual {{v1, v2}}, {INTENT}->setPackage(Ljava/lang/String;){INTENT}
"""
CHOOSER = f"""
    const/4 v2, 0x0
    invoke-static {{v1, v2}}, {INTENT}->createChooser({INTENT}Ljava/lang/CharSequence;){INTENT}
    move-result-object v1
"""


class IntentGuardTests(unittest.TestCase):
    def test_direct_private_grant_detects_read_write_and_fluid_register_aliases(self):
        for block in (BLOCK, BLOCK.replace("0x1", "0x2"),
                      BLOCK.replace(START, "    move-object v3, v1\n" + START.replace("v1", "v3"))):
            self.assertEqual(direct_private_grants(block.encode(), PACKAGE), Counter({"share()V": 1}))

    def test_scoped_or_legitimate_open_ended_shares_are_not_blocked(self):
        for block in (
            BLOCK.replace(START, SCOPED + START),
            BLOCK.replace(START, CHOOSER + START),
            BLOCK.replace(f"{PACKAGE}.OPEN_RECORD", "android.intent.action.VIEW"),
            BLOCK.replace("0x1", "0x4"),
            BLOCK.replace(START, f"    invoke-virtual {{v1, v2}}, {INTENT}->removeFlags(I)V\n" + START),
            BLOCK.replace(START, f"    invoke-static {{v1}}, Lexample/Helper;->configure({INTENT})V\n" + START),
            BLOCK.replace(START, "    :unknown_path\n" + START),
        ):
            self.assertFalse(direct_private_grants(block.encode(), PACKAGE))

    def test_rejected_edit_leaves_previous_candidate_and_build_budget_intact(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            name = "smali/example/Owner.smali"
            session = Session(root, root / "source.apk", root / "candidate.apk", root, 36,
                              {"package": PACKAGE}, lambda _: None, None)
            session.built(root / "known-good.apk", {"sha256": "prior"})
            session.workspace.create_text_file(name, BLOCK)
            with patch.object(session, "start_build") as start:
                with self.assertRaisesRegex(ValueError, r"share\(\)V.*接收方未限定"):
                    session.dispatch("build_apk", {})
                start.assert_not_called()
            self.assertEqual(session.build_count, 0)
            self.assertEqual(session.artifact.name, "known-good.apk")
            entry = session.workspace.read_text(name)
            session.workspace.replace_text(name, entry["sha256"], START, SCOPED + START)
            with patch.object(session, "start_build", side_effect=RuntimeError("builder reached")):
                with self.assertRaisesRegex(RuntimeError, "builder reached"):
                    session.dispatch("build_apk", {})

    def test_existing_direct_grant_does_not_block_an_unrelated_edit(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "smali/example/Owner.smali"
            path.parent.mkdir(parents=True)
            path.write_text(BLOCK, encoding="utf-8")
            session = Session(root, root / "source.apk", root / "candidate.apk", root, 36,
                              {"package": PACKAGE}, lambda _: None, None)
            name = path.relative_to(root).as_posix()
            entry = session.workspace.read_text(name)
            session.workspace.replace_text(name, entry["sha256"], ".line 42", ".line 43")
            with patch.object(session, "start_build", side_effect=RuntimeError("builder reached")):
                with self.assertRaisesRegex(RuntimeError, "builder reached"):
                    session.dispatch("build_apk", {})
