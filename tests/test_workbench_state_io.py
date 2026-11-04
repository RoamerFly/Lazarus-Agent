"""Windows-safe persistence regression checks for workbench job state."""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import workbench


class WorkbenchStateIoTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)

    def test_save_retries_unique_temp_after_transient_replace_denial(self):
        state={"id":"job","status":"ready"}
        real_replace=workbench.os.replace
        calls=[]

        def replace_once_denied(source,target):
            calls.append(Path(source).name)
            if len(calls)==1:
                raise PermissionError(5,"access denied",str(target))
            return real_replace(source,target)

        with patch.object(workbench.os,"replace",side_effect=replace_once_denied), \
             patch.object(workbench,"_state_retry_pause"):
            workbench.save(self.folder,state)

        self.assertEqual(workbench.read(self.folder),state)
        self.assertEqual(len(calls),2)
        self.assertNotEqual(calls[0],calls[1])
        self.assertEqual(list(self.folder.glob("*.tmp")),[])

    def test_read_retries_then_reports_persistent_access_denial(self):
        state={"id":"job","status":"ready"}
        workbench.save(self.folder,state)
        real_read=workbench._read_state_file
        calls=[]

        def read_once_denied(path):
            calls.append(path)
            if len(calls)<3:
                raise PermissionError(5,"access denied",str(path))
            return real_read(path)

        with patch.object(workbench,"_read_state_file",side_effect=read_once_denied), \
             patch.object(workbench,"_state_retry_pause"):
            self.assertEqual(workbench.read(self.folder),state)
        self.assertEqual(len(calls),3)

        with patch.object(workbench,"_read_state_file",side_effect=PermissionError(5,"access denied")), \
             patch.object(workbench,"_state_retry_pause"):
            with self.assertRaises(PermissionError):
                workbench.read(self.folder)

