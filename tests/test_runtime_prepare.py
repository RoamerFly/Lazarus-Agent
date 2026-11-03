import tempfile
from pathlib import Path
import threading
import time
import unittest

from runtime_prepare import MAX_ACTIVE_EVENTS, RuntimePreparation


class RuntimePreparationTests(unittest.TestCase):
    def test_consent_is_required_and_existing_runtime_does_not_prepare(self):
        ready=lambda api: {"api":api,"ready":True}
        with tempfile.TemporaryDirectory() as directory:
            manager=RuntimePreparation(inspect=ready,state_file=Path(directory)/"consent.json")
            result=manager.start([36],False)
        self.assertTrue(result["ready"])

    def test_missing_runtime_needs_explicit_consent_then_runs_serially(self):
        calls=[]
        def inspect(api): return {"api":api,"ready":False}
        def prepare(api,progress,**kwargs):
            calls.append(api); progress({"phase":"ready","component":"runtime","message":"done"})
        with tempfile.TemporaryDirectory() as directory:
            manager=RuntimePreparation(inspect=inspect,prepare=prepare,state_file=Path(directory)/"consent.json")
            with self.assertRaises(PermissionError): manager.start([34],False)
            manager.start([34,36],True)
            for _ in range(50):
                if manager.status()["active"]["status"]!="running": break
                time.sleep(.01)
            self.assertEqual(calls,[34,36])
            self.assertTrue(manager.status()["terms_accepted"])

    def test_cancel_sets_event_for_prepare(self):
        started=threading.Event()
        def inspect(api): return {"api":api,"ready":False}
        def prepare(api,progress,cancel,**kwargs):
            started.set(); cancel.wait(.5)
            if cancel.is_set(): raise InterruptedError("cancelled")
        with tempfile.TemporaryDirectory() as directory:
            manager=RuntimePreparation(inspect=inspect,prepare=prepare,state_file=Path(directory)/"consent.json")
            manager.start([35],True); self.assertTrue(started.wait(.3)); self.assertTrue(manager.cancel()["cancelled"])
            for _ in range(50):
                if manager.status()["active"]["status"]!="running": break
                time.sleep(.01)
            self.assertEqual(manager.status()["active"]["status"],"cancelled")

    def test_download_progress_is_bounded_in_active_state(self):
        release=threading.Event()
        def inspect(api): return {"api":api,"ready":False}
        def prepare(api,progress,**kwargs):
            for number in range(MAX_ACTIVE_EVENTS + 25):
                progress({"phase":"download","component":"system-image","message":"chunk","current":number,"total":999})
            release.wait(.3)
        with tempfile.TemporaryDirectory() as directory:
            manager=RuntimePreparation(inspect=inspect,prepare=prepare,state_file=Path(directory)/"consent.json")
            manager.start([36],True)
            for _ in range(50):
                active=manager.status()["active"]
                if active and len(active["events"]) >= 20: break
                time.sleep(.01)
            self.assertLessEqual(len(manager.active["events"]),MAX_ACTIVE_EVENTS)
            self.assertEqual(len(manager.status()["active"]["events"]),20)
            release.set()
