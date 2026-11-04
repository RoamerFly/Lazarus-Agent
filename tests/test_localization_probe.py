import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import localization_probe


FACTS = {"package": "org.example.reader", "launcher": ".ReaderActivity"}
CRITERIA = {"checks": [{"original": "Open record", "translated": "打开记录"},
                       {"original": "Current", "translated": "当前"}]}


class LocaleProbeTests(unittest.TestCase):
    def test_rejects_missing_checks(self):
        with self.assertRaisesRegex(ValueError, "checks"):
            localization_probe._validate(FACTS, {})

    def test_unparseable_locale_reply_is_insufficient_without_setting(self):
        commands = []
        class Result:
            returncode = 0
            stdout = b""
            stderr = b""
        with tempfile.TemporaryDirectory() as temp, patch("localization_probe.shell", return_value="LocaleList[en-US]"), \
             patch("localization_probe.invoke", side_effect=lambda *args: commands.append(args) or Result()):
            result = localization_probe.verify_locale("emulator-1", FACTS, CRITERIA, Path(temp) / "proof")
        self.assertEqual(result["result"], "insufficient")
        self.assertFalse(any("set-app-locales" in call[0] for call in commands))

    def test_chinese_missing_text_is_negative_and_locale_is_reset(self):
        trees = iter([
            b'<hierarchy><node package="org.example.reader" text="Open record"/><node package="org.example.reader" text="Current"/></hierarchy>',
            b'<hierarchy><node package="org.example.reader" text="Open record"/><node package="org.example.reader" text="Current"/></hierarchy>',
        ])
        commands = []
        locale_state = {"values": ()}
        class Result:
            returncode = 0
            stderr = b""
            def __init__(self, stdout=b""): self.stdout = stdout
        def shell(serial, *parts, **unused):
            if parts[:3] == ("cmd", "locale", "get-app-locales"):
                return "Locales for org.example.reader for user 0 are [" + ",".join(locale_state["values"]) + "]"
            return ""
        def invoke(args, *unused):
            commands.append(args)
            if "set-app-locales" in args:
                locale_state["values"] = tuple(args[args.index("--locales") + 1].split(",")) if "--locales" in args else ()
            if "exec-out" in args and "cat" in args: return Result(next(trees))
            if "exec-out" in args: return Result()
            if "dumpsys" in args: return Result(b"mCurrentFocus=Window{1 u0 org.example.reader/.ReaderActivity}")
            return Result(b"Status: ok")
        with tempfile.TemporaryDirectory() as temp, patch("localization_probe.shell", side_effect=shell), \
             patch("localization_probe.invoke", side_effect=invoke), patch("localization_probe.time.sleep"):
            result = localization_probe.verify_locale("emulator-1", FACTS, CRITERIA, Path(temp) / "proof")
        self.assertEqual(result["result"], "failed")
        self.assertEqual(result["failed_step"], "zh-CN:1")
        sets = [args[-1] for args in commands if "set-app-locales" in args and "--locales" in args]
        self.assertEqual(sets[:2], ["en-US", "zh-CN"])
        self.assertTrue(result["restored"])

    def test_operation_and_return_to_english_are_checked(self):
        trees = iter([
            b'<hierarchy><node package="org.example.reader" text="Open"/></hierarchy>',
            '<hierarchy><node package="org.example.reader" text="打开" bounds="[0,0][4,4]"/></hierarchy>'.encode(),
            '<hierarchy><node package="org.example.reader" text="打开" bounds="[0,0][4,4]"/></hierarchy>'.encode(),
            '<hierarchy><node package="org.example.reader" text="完成"/></hierarchy>'.encode(),
            b'<hierarchy><node package="org.example.reader" text="Open"/></hierarchy>',
        ])
        locale_state = {"values": ()}
        class Result:
            returncode = 0
            stderr = b""
            def __init__(self, stdout=b""): self.stdout = stdout
        def shell(serial, *parts, **unused):
            if parts[:3] == ("cmd", "locale", "get-app-locales"):
                return "Locales for org.example.reader for user 0 are [" + ",".join(locale_state["values"]) + "]"
            return ""
        def invoke(args, *unused):
            if "set-app-locales" in args:
                locale_state["values"] = tuple(args[args.index("--locales") + 1].split(",")) if "--locales" in args else ()
            if "exec-out" in args and "cat" in args: return Result(next(trees))
            if "exec-out" in args: return Result()
            if "dumpsys" in args: return Result(b"mCurrentFocus=Window{1 u0 org.example.reader/.ReaderActivity}")
            return Result(b"Status: ok")
        criteria = {"checks": [{"original": "Open", "translated": "打开"}],
                    "operation": {"tap": "打开", "expect": "完成"}}
        with tempfile.TemporaryDirectory() as temp, patch("localization_probe.shell", side_effect=shell), \
             patch("localization_probe.invoke", side_effect=invoke), patch("localization_probe.time.sleep"):
            result = localization_probe.verify_locale("emulator-1", FACTS, criteria, Path(temp) / "proof")
        self.assertEqual(result["result"], "passed")
        self.assertEqual([step["phase"] for step in result["steps"]],
                         ["en-US", "zh-CN", "zh-CN-operation", "en-US-return"])

    def test_nonforeground_is_insufficient(self):
        tree = b'<hierarchy><node package="android" text="Home"/></hierarchy>'
        class Result:
            returncode = 0
            stderr = b""
            def __init__(self, stdout=b""): self.stdout = stdout
        def invoke(args, *unused):
            if "exec-out" in args and "cat" in args: return Result(tree)
            if "exec-out" in args: return Result()
            if "dumpsys" in args: return Result(b"mCurrentFocus=Window{1 u0 com.android.launcher/.Home}")
            return Result(b"Status: ok")
        def shell(serial, *parts, **unused):
            return "Locales for org.example.reader for user 0 are [en-US]" if parts[:3] == ("cmd", "locale", "get-app-locales") else ""
        with tempfile.TemporaryDirectory() as temp, patch("localization_probe.shell", side_effect=shell), \
             patch("localization_probe.invoke", side_effect=invoke), patch("localization_probe.time.sleep"):
            result = localization_probe.verify_locale("emulator-1", FACTS, CRITERIA, Path(temp) / "proof")
        self.assertEqual(result["result"], "insufficient")

    def test_english_baseline_without_original_is_insufficient(self):
        tree = b'<hierarchy><node package="org.example.reader" text="Unknown"/></hierarchy>'
        class Result:
            returncode = 0
            stderr = b""
            def __init__(self, stdout=b""): self.stdout = stdout
        def shell(serial, *parts, **unused):
            return "Locales for org.example.reader for user 0 are [en-US]" if parts[:3] == ("cmd", "locale", "get-app-locales") else ""
        def invoke(args, *unused):
            if "exec-out" in args and "cat" in args: return Result(tree)
            if "exec-out" in args: return Result()
            if "dumpsys" in args: return Result(b"mCurrentFocus=Window{1 u0 org.example.reader/.ReaderActivity}")
            return Result(b"Status: ok")
        with tempfile.TemporaryDirectory() as temp, patch("localization_probe.shell", side_effect=shell), \
             patch("localization_probe.invoke", side_effect=invoke), patch("localization_probe.time.sleep"):
            result = localization_probe.verify_locale("emulator-1", FACTS, CRITERIA, Path(temp) / "proof")
        self.assertEqual(result["result"], "insufficient")
        self.assertEqual(result["failed_step"], "en-US:1")

    def test_operation_preexisting_expectation_is_insufficient(self):
        trees = iter([
            b'<hierarchy><node package="org.example.reader" text="Open"/></hierarchy>',
            '<hierarchy><node package="org.example.reader" text="打开" bounds="[0,0][4,4]"/><node package="org.example.reader" text="完成"/></hierarchy>'.encode(),
            '<hierarchy><node package="org.example.reader" text="打开" bounds="[0,0][4,4]"/><node package="org.example.reader" text="完成"/></hierarchy>'.encode(),
        ])
        state = {"value": ()}
        class Result:
            returncode = 0
            stderr = b""
            def __init__(self, stdout=b""): self.stdout = stdout
        def shell(serial, *parts, **unused):
            if parts[:3] == ("cmd", "locale", "get-app-locales"):
                return "Locales for org.example.reader for user 0 are [" + ",".join(state["value"]) + "]"
            return ""
        def invoke(args, *unused):
            if "set-app-locales" in args:
                state["value"] = tuple(args[args.index("--locales") + 1].split(",")) if "--locales" in args else ()
            if "exec-out" in args and "cat" in args: return Result(next(trees))
            if "exec-out" in args: return Result()
            if "dumpsys" in args: return Result(b"mCurrentFocus=Window{1 u0 org.example.reader/.ReaderActivity}")
            return Result(b"Status: ok")
        criteria = {"checks": [{"original": "Open", "translated": "打开"}], "operation": {"tap": "打开", "expect": "完成"}}
        with tempfile.TemporaryDirectory() as temp, patch("localization_probe.shell", side_effect=shell), \
             patch("localization_probe.invoke", side_effect=invoke), patch("localization_probe.time.sleep"):
            result = localization_probe.verify_locale("emulator-1", FACTS, criteria, Path(temp) / "proof")
        self.assertEqual(result["result"], "insufficient")
        self.assertEqual(result["failed_step"], "zh-CN-operation")
