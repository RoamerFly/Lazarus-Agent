"""Regression checks for the shared modification workflow independent of an Android lab."""
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import Mock, patch

import modification_runner as runner
from modification_tasks import make_plan


class ModificationRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)
        (self.folder/"original.apk").write_bytes(b"original")
        self.state={"api":36,"status":"ready","package":{"sha256":runner.digest(self.folder/"original.apk"),
                     "native_abis":[]},"events":[]}
        body={"request":"记住上次打开的文件","targets":[34,36],"criteria":{"selects":[
            {"tap":"B","expect":"second"},{"tap":"A","expect":"first"}]}}
        plan=make_plan(self.state,body)
        self.state["modification"]={"plan":plan,"runs":[]}
        self.plan=plan

    def run_case(self,outcomes):
        runner.start_run(self.state,self.plan["id"])
        adapter=Mock()
        adapter.inspect.return_value={"available":True,"plan":{"location":"verified"}}
        adapter.apply.return_value={"kind":"remember-file"}
        def build(source,dest,api,tools,transform):
            audit=transform(dest.parent)
            dest.write_bytes(audit["kind"].encode())
            return {"kind":audit["kind"],"sha256":runner.digest(dest)}
        def installed(device,owner,api,facts,apk,log):
            return "avd",{"installed_sha256":runner.digest(apk),"verified_environment":{"sdk":api}}
        verify=Mock(side_effect=[{"result":s,"message":s,"failed_step":1} for s in outcomes])
        lock=threading.Lock()
        lock.acquire()
        with patch("apk_edit_build.build_modified",side_effect=build) as builder, \
             patch.object(runner,"evidence_install",side_effect=installed), \
             patch.object(runner,"capability",return_value=(adapter,verify)):
            runner.run_worker(self.folder,self.state,Mock(),Mock(),lambda *a:["verified"],lambda:"now",lock)
        self.assertFalse(lock.locked())
        return builder

    def test_candidate_requires_all_targets_and_same_hash(self):
        self.run_case(["failed","failed","passed","passed"])
        self.assertEqual(self.state["modification"]["status"],"passed")
        accepted=self.state["export"]["sha256"]
        self.assertEqual(accepted,runner.digest(self.folder/"versions/1.apk"))
        self.assertTrue(all(r["installed_sha256"]==accepted for r in self.state["compatibility"]["rows"]))
        self.assertEqual(runner.digest(self.folder/"original.apk"),self.plan["source_sha256"])

    def test_one_failed_target_blocks_export(self):
        self.run_case(["failed","failed","passed","failed"])
        self.assertIsNone(self.state["export"])
        self.assertEqual([r["status"] for r in self.state["compatibility"]["rows"]],["failed","passed"])

    def test_unobservable_baseline_or_changed_control_never_runs_edit(self):
        for outcomes,count in [(["insufficient"],0),(["failed","passed"],1)]:
            with self.subTest(outcomes=outcomes):
                self.assertEqual(self.run_case(outcomes).call_count,count)
                self.assertIsNone(self.state["export"])

    def test_already_satisfied_checks_all_targets_without_build(self):
        self.assertEqual(self.run_case(["passed","passed"]).call_count,0)
        self.assertEqual(self.state["export"]["sha256"],self.plan["source_sha256"])

    def test_changed_source_and_stale_plan_do_not_run(self):
        with self.assertRaises(ValueError):
            runner.start_run(self.state,"stale-id")
        (self.folder/"original.apk").write_bytes(b"new")
        self.assertEqual(self.run_case([]).call_count,0)
        self.assertEqual(self.state["status"],"error")
