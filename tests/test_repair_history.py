import hashlib
from pathlib import Path
import tempfile
import unittest
from fastapi import HTTPException

from repair_history import archive_version, record_feedback


class HistoryTests(unittest.TestCase):
    def test_retry_keeps_old_bytes_and_resolves_related_issue(self):
        with tempfile.TemporaryDirectory() as data:
            root=Path(data)
            def export(content):
                (root/"candidate.apk").write_bytes(content)
                return {"file":"candidate.apk","sha256":hashlib.sha256(content).hexdigest(),"level":"指定操作已验证"}
            state={"export":export(b"first")}
            archive_version(root,state)
            record_feedback(root,state,{"kind":"issue","version":1,"text":"点击“读取”后应显示“完成”，实际闪退。"})
            self.assertEqual(state["pending_feedback"]["task"],{"tap":"读取","expect":"完成"})
            state["attempt_feedback"]=state["pending_feedback"]["id"]
            state["export"]=export(b"second")
            archive_version(root,state)
            archive_version(root,state)
            self.assertEqual(len(state["versions"]),2)
            self.assertEqual((root/"versions/1.apk").read_bytes(),b"first")
            record_feedback(root,state,{"kind":"works","version":2,"text":"复测通过"})
            self.assertNotIn("pending_feedback",state)
            self.assertEqual(state["versions"][1]["phone"],"用户确认可运行")

    def test_unverified_file_and_nonexistent_version_are_rejected(self):
        with tempfile.TemporaryDirectory() as data:
            root=Path(data)
            (root/"candidate.apk").write_bytes(b"bad")
            state={"export":{"file":"candidate.apk","sha256":"0"*64,"level":"test"}}
            with self.assertRaises(ValueError):
                archive_version(root,state)
            self.assertFalse(list((root/"versions").glob("*.apk")))
            with self.assertRaises(HTTPException):
                record_feedback(root,state,{"text":"好了","kind":"works","version":99})
