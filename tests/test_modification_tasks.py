import unittest

from modification_tasks import make_plan


class ModificationPlanTests(unittest.TestCase):
    def setUp(self):
        self.state = {"api": 36, "package": {"sha256": "a"*64},
                      "versions": [{"number": 1, "sha256": "b"*64}]}
        self.body = {"request":"重开时记住上次看的文件", "criteria":{"selects":[
            {"tap":"乙","expect":"乙文件正文"}, {"tap":"甲","expect":"甲文件正文"}]}}

    def test_pins_input_and_target(self):
        plan = make_plan(self.state, dict(self.body, base_version=1,targets=[34,36]))
        self.assertEqual((plan["kind"],plan["source_sha256"],plan["targets"]),("remember-file","b"*64,[34,36]))
        self.assertEqual(make_plan(self.state,self.body)["source_sha256"],"a"*64)

    def test_rejects_unobservable_or_unsupported_changes(self):
        for override in ({"request":"恢复云同步"}, {"request":"不要再记住文件"}, {"base_version":True},
                         {"targets":[34]}, {"criteria":{"selects":[{"tap":"A","expect":"x"}]*2}}):
            with self.subTest(override=override), self.assertRaises(ValueError):
                make_plan(self.state,dict(self.body,**override))
