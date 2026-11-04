import unittest
from unittest.mock import patch

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

    def test_model_routes_only_with_explicit_opt_in(self):
        from model_tools import ModelToolError
        reply={"tool_calls":[{"function":{"name":"choose_modification","arguments":'{"kind":"remember-file"}'}}]}
        with patch("model_settings.get_config",return_value={"url":"http://localhost","model":"fake","key":""}), \
             patch("model_tools._request",return_value=reply) as request:
            self.assertEqual(make_plan(self.state,self.body)["tool_source"],"rules")
            request.assert_not_called()
            plan=make_plan(self.state,dict(self.body,use_model=True))
            self.assertEqual(plan["tool_source"],"model")
            self.assertNotIn("source_sha256",str(request.call_args))
            request.side_effect=ModelToolError("unavailable")
            self.assertEqual(make_plan(self.state,dict(self.body,use_model=True))["tool_source"],"fallback")

    def test_language_checks_can_be_a_visible_subset(self):
        criteria = {"translations": {"Tea timer": "泡茶计时", "Start": "开始"},
                    "checks": [{"original": "Start", "translated": "开始"}]}
        body = {"request": "把界面汉化", "criteria": criteria}
        plan = make_plan(self.state, body)
        self.assertEqual(plan["kind"], "localize-zh")
        self.assertEqual(plan["criteria"]["checks"], criteria["checks"])
        for overrides in ({"checks": []}, {"checks": [{"original": "x", "translated": "开始"}]},
                          {"resources": {"Start": "../bad"}}, {"operation": {"tap": "开始"}}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                make_plan(self.state, dict(body, criteria=dict(criteria, **overrides)))


    def test_menu_path_persists_only_supported_steps(self):
        criteria = {"translations": {"Character count": "字符计数"},
                    "checks": [{"original": "Character count", "translated": "字符计数"}],
                    "route": ["menu", {"en": "Settings", "zh": "设置"}]}
        body = {"kind": "localize-zh", "request": "汉化设置", "criteria": criteria}
        self.assertEqual(make_plan(self.state, body)["criteria"]["route"],
                         ["menu", {"en": "Settings", "zh": "设置", "optional": False}])
        for route in (["shell input keyevent 4"], ["launch"] * 13, [{"en": "Settings"}],
                      [{"en": "Settings", "zh": "设置", "optional": "yes"}]):
            with self.subTest(route=route), self.assertRaises(ValueError):
                make_plan(self.state, dict(body, criteria=dict(criteria, route=route)))
