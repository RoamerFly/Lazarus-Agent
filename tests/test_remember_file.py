import tempfile
from pathlib import Path
import unittest

from remember_file import MARKER, RememberFileError, apply, inspect


SIMPLE = '''.class public Lcom/example/ReaderActivity;
.super Landroid/app/Activity;

.method protected onCreate(Landroid/os/Bundle;)V
    .locals 2
    const-string v0, "A"
    invoke-direct {p0, v0}, Lcom/example/ReaderActivity;->openRecord(Ljava/lang/String;)V
    return-void
.end method

.method public onClick(Landroid/view/View;)V
    .locals 1
    invoke-virtual {p1}, Landroid/view/View;->getTag()Ljava/lang/Object;
    move-result-object v0
    check-cast v0, Ljava/lang/String;
    invoke-direct {p0, v0}, Lcom/example/ReaderActivity;->openRecord(Ljava/lang/String;)V
    return-void
.end method

.method private openRecord(Ljava/lang/String;)V
    .locals 1
    invoke-virtual {p0, p1}, Landroid/content/Context;->openFileInput(Ljava/lang/String;)Ljava/io/FileInputStream;
    move-result-object v0
    return-void
.end method
'''


class RememberFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.file = self.root / "smali/com/example/ReaderActivity.smali"
        self.file.parent.mkdir(parents=True)
        self.file.write_text(SIMPLE, encoding="utf-8")
        self.addCleanup(self.temp.cleanup)

    def test_inspect_then_apply_preserves_picker_path(self):
        result = inspect(self.root)
        self.assertTrue(result["available"])
        self.assertEqual(result["plan"]["default_record"], "A")
        applied = apply(self.root, result["plan"])
        text = self.file.read_text(encoding="utf-8")
        self.assertTrue(applied["applied"])
        self.assertEqual(applied["kind"], "remember-last-record-v1")
        self.assertIn(MARKER, text)
        self.assertIn("lazarusLoadLastRecord(Ljava/lang/String;)Ljava/lang/String;", text)
        self.assertIn("lazarusSaveLastRecord(Ljava/lang/String;)V", text)
        self.assertIn("move-result-object v0\n    invoke-direct {p0, v1}", text)
        # The picker still supplies its runtime String tag through the same
        # target.  Saving at the target entry catches it without hard-coding a
        # particular second record.
        self.assertIn("check-cast v0, Ljava/lang/String;\n    invoke-direct {p0, v0}, Lcom/example/ReaderActivity;->openRecord", text)
        self.assertFalse(inspect(self.root)["available"])

    def test_multiple_startup_paths_are_refused(self):
        self.file.write_text(SIMPLE.replace(
            '    return-void\n.end method\n\n.method public onClick',
            '    const-string v1, "B"\n    invoke-direct {p0, v1}, Lcom/example/ReaderActivity;->openRecord(Ljava/lang/String;)V\n    return-void\n.end method\n\n.method public onClick', 1), encoding="utf-8")
        result = inspect(self.root)
        self.assertFalse(result["available"])
        self.assertIn("多个", result["reason"])

    def test_requires_direct_activity_parent(self):
        self.file.write_text(SIMPLE.replace(".super Landroid/app/Activity;", ".super Landroidx/appcompat/app/AppCompatActivity;"), encoding="utf-8")
        self.assertFalse(inspect(self.root)["available"])

    def test_tampered_plan_never_writes(self):
        plan = inspect(self.root)["plan"]
        plan["target_method"] = "deleteEverything"
        with self.assertRaisesRegex(RememberFileError, "不一致"):
            apply(self.root, plan)
        self.assertNotIn(MARKER, self.file.read_text(encoding="utf-8"))

    def test_requires_literal_directly_before_open(self):
        self.file.write_text(SIMPLE.replace('    const-string v0, "A"\n', '    const-string v0, "A"\n    const/4 v0, 0x0\n'), encoding="utf-8")
        self.assertFalse(inspect(self.root)["available"])

    def test_accepts_literal_in_same_straight_line_block(self):
        self.file.write_text(SIMPLE.replace('    invoke-direct {p0, v0}',
                                             '    invoke-virtual {p0}, Landroid/app/Activity;->getTitle()Ljava/lang/CharSequence;\n'
                                             '    move-result-object v1\n'
                                             '    invoke-direct {p0, v0}', 1), encoding="utf-8")
        self.assertTrue(inspect(self.root)["available"])

    def test_rejects_ui_method_without_real_file_access(self):
        self.file.write_text(SIMPLE.replace(
            '    invoke-virtual {p0, p1}, Landroid/content/Context;->openFileInput(Ljava/lang/String;)Ljava/io/FileInputStream;\n    move-result-object v0\n',
            '    invoke-virtual {p0, p1}, Landroid/app/Activity;->setTitle(Ljava/lang/CharSequence;)V\n'), encoding="utf-8")
        self.assertFalse(inspect(self.root)["available"])

    def test_rejects_high_oncreate_register_for_nonrange_restore(self):
        self.file.write_text(SIMPLE.replace("    .locals 2\n    const-string v0", "    .locals 16\n    const-string v0", 1), encoding="utf-8")
        self.assertFalse(inspect(self.root)["available"])

    def test_rejects_registers_method_using_absolute_parameter_slot(self):
        self.file.write_text(SIMPLE.replace(
            '    .locals 1\n    invoke-virtual {p0, p1}, Landroid/content/Context;->openFileInput',
            '    .registers 3\n    move-object v2, p1\n    invoke-virtual {p0, p1}, Landroid/content/Context;->openFileInput'), encoding="utf-8")
        self.assertFalse(inspect(self.root)["available"])

    def test_rejects_existing_helper_name_even_without_comment(self):
        self.file.write_text(SIMPLE + '''
.method private lazarusSaveLastRecord(Ljava/lang/String;)V
    .locals 0
    return-void
.end method
''', encoding="utf-8")
        self.assertFalse(inspect(self.root)["available"])

    def test_requires_an_alternative_selection_caller(self):
        text = self.file.read_text(encoding="utf-8")
        start = text.index(".method public onClick")
        end = text.index(".end method", start) + len(".end method\n")
        self.file.write_text(text[:start] + text[end:], encoding="utf-8")
        result = inspect(self.root)
        self.assertFalse(result["available"])
        self.assertIn("切换", result["reason"])

    def test_preserves_original_parameter_when_result_reuses_p1(self):
        self.file.write_text(SIMPLE.replace(
            '    invoke-virtual {p0, p1}, Landroid/content/Context;->openFileInput(Ljava/lang/String;)Ljava/io/FileInputStream;',
            '    const-string p1, "different-record"\n'
            '    invoke-virtual {p0, p1}, Landroid/content/Context;->openFileInput(Ljava/lang/String;)Ljava/io/FileInputStream;'),
            encoding="utf-8")
        apply(self.root, inspect(self.root)["plan"])
        changed = self.file.read_text(encoding="utf-8")
        self.assertIn("const-string p1, \"different-record\"\n    move-object v1, p1", changed)
        self.assertIn("move-result-object v0\n    invoke-direct {p0, v1}", changed)
        self.assertNotIn("move-object p1, v1", changed)

