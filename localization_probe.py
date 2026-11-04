"""Evidence-first app-locale acceptance for an isolated Android APK test."""
from __future__ import annotations

from pathlib import Path
import re
import time
import xml.etree.ElementTree as ET

from bench_device import ADB, invoke, shell

_PACKAGE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+")
_ACTIVITY = re.compile(r"[A-Za-z0-9_.$]+")
_MAX_CHECKS = 100
_LANGUAGE = re.compile(r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*")


def _validate(facts: dict, criteria: dict) -> tuple[str, str, list[dict], dict | None]:
    if not isinstance(facts, dict) or not isinstance(criteria, dict):
        raise ValueError("语言验收输入格式无效")
    package, launcher = facts.get("package"), facts.get("launcher")
    if not isinstance(package, str) or not _PACKAGE.fullmatch(package):
        raise ValueError("APK 包名无效")
    if not isinstance(launcher, str) or not _ACTIVITY.fullmatch(launcher):
        raise ValueError("APK 启动 Activity 无效")
    checks = criteria.get("checks")
    if not isinstance(checks, list) or not 1 <= len(checks) <= _MAX_CHECKS:
        raise ValueError("语言验收需要 1 至 %d 条 checks" % _MAX_CHECKS)
    rows = []
    for item in checks:
        if not isinstance(item, dict):
            raise ValueError("语言检查项格式无效")
        original, translated = item.get("original"), item.get("translated")
        if not all(isinstance(value, str) and 0 < len(value.strip()) <= 500 for value in (original, translated)):
            raise ValueError("每条检查均需 original 与 translated")
        rows.append({"original": original.strip(), "translated": translated.strip()})
    operation = criteria.get("operation")
    if operation is not None:
        if not isinstance(operation, dict) or not all(isinstance(operation.get(key), str) and 0 < len(operation[key].strip()) <= 500 for key in ("tap", "expect")):
            raise ValueError("operation 需要 tap 与 expect")
        operation = {"tap": operation["tap"].strip(), "expect": operation["expect"].strip()}
    return package, launcher, rows, operation


def verify_locale(serial: str, facts: dict, criteria: dict, dest: Path) -> dict:
    """Check en-US, zh-CN, optional Chinese operation, then English return."""
    package, launcher, checks, operation = _validate(facts, criteria)
    if not isinstance(serial, str) or not serial:
        raise ValueError("测试设备无效")
    dest.mkdir(parents=True, exist_ok=False)
    steps: list[dict] = []

    def get_locales() -> tuple[str, ...]:
        raw = shell(serial, "cmd", "locale", "get-app-locales", package, "--user", "0").strip()
        match = re.fullmatch(r"Locales for " + re.escape(package) + r" for user 0 are \[([^\]]*)\]", raw)
        if not match:
            raise RuntimeError("无法解析系统返回的应用语言列表")
        values = tuple(item.strip() for item in match.group(1).split(",") if item.strip())
        if len(values) != len(set(values)) or not all(_LANGUAGE.fullmatch(item) for item in values):
            raise RuntimeError("系统返回的应用语言列表无效")
        return values

    def set_locales(locales: tuple[str, ...]) -> None:
        command = [ADB, "-s", serial, "shell", "cmd", "locale", "set-app-locales", package, "--user", "0"]
        if locales:
            command.extend(["--locales", ",".join(locales)])
        answer = invoke(command, 30)
        if answer.returncode:
            raise RuntimeError("系统拒绝切换应用语言：" + (answer.stderr + answer.stdout).decode("utf-8", "replace")[-300:])

    def snapshot(label: str) -> ET.Element:
        remote = "/data/local/tmp/lazarus-locale-ui.xml"
        shell(serial, "rm", "-f", remote)
        shell(serial, "uiautomator", "dump", remote)
        data = invoke([ADB, "-s", serial, "exec-out", "cat", remote]).stdout
        if not data:
            raise RuntimeError("无法读取设备界面树")
        (dest / (label + ".xml")).write_bytes(data)
        image = invoke([ADB, "-s", serial, "exec-out", "screencap", "-p"]).stdout
        if image.startswith(b"\x89PNG"):
            (dest / (label + ".png")).write_bytes(image)
        try:
            return ET.fromstring(data)
        except ET.ParseError as exc:
            raise RuntimeError("设备返回的界面树无效") from exc

    def owned(tree: ET.Element) -> list[ET.Element]:
        return [node for node in tree.iter("node") if node.get("package") == package]

    def has(nodes: list[ET.Element], text: str) -> bool:
        return any(text in {node.get("text"), node.get("content-desc"), node.get("resource-id")} for node in nodes)

    def start(locale: str) -> tuple[bool, bool, ET.Element]:
        set_locales((locale,))
        time.sleep(.8)
        applied = get_locales() == (locale,)
        shell(serial, "am", "force-stop", package)
        launched = invoke([ADB, "-s", serial, "shell", "am", "start", "-W", "-n", package + "/" + launcher], 30)
        if launched.returncode:
            raise RuntimeError("切换应用语言后启动失败")
        time.sleep(.7)
        windows = invoke([ADB, "-s", serial, "shell", "dumpsys", "window", "displays"], 15)
        current = (windows.stdout + windows.stderr).decode("utf-8", "replace")
        match = re.search(r"(?m)^\s*mCurrentFocus=Window\{[^\r\n}]*?\bu\d+\s+([\w.]+)/", current)
        return bool(match and match.group(1) == package), applied, snapshot(locale)

    def phase(label: str, locale: str, field: str) -> dict | None:
        foreground, applied, tree = start(locale)
        nodes = owned(tree)
        matches = [has(nodes, row[field]) for row in checks]
        steps.append({"phase": label, "foreground": foreground, "locale_applied": applied,
                      "app_nodes": bool(nodes), "matches": matches})
        if not applied:
            return {"result": "insufficient", "message": "系统未确认应用语言已切换到 %s" % label,
                    "steps": steps, "failed_step": label}
        if not foreground or not nodes:
            return {"result": "insufficient", "message": "切换到 %s 后目标应用不在前台" % label, "steps": steps, "failed_step": label}
        if not all(matches):
            missing = next(index + 1 for index, match in enumerate(matches) if not match)
            if field == "translated" and has(nodes, checks[missing - 1]["original"]):
                return {"result": "failed", "message": "%s 下第 %d 条文字仍为原文" % (label, missing),
                        "steps": steps, "failed_step": label + ":" + str(missing), "failed_locale": "zh-CN"}
            return {"result": "insufficient", "message": "%s 下缺少第 %d 条可对照文字" % (label, missing),
                    "steps": steps, "failed_step": label + ":" + str(missing)}
        return None

    original: tuple[str, ...] = ()
    read_original = False
    result: dict | None = None
    restored = False
    try:
        original = get_locales()
        read_original = True
        result = phase("en-US", "en-US", "original")
        if result is None:
            result = phase("zh-CN", "zh-CN", "translated")
        if result is None and operation:
            before = snapshot("zh-CN-before-operation")
            target = next((node for node in owned(before) if operation["tap"] in {node.get("text"), node.get("content-desc"), node.get("resource-id")}), None)
            if target is None:
                result = {"result": "insufficient", "message": "找不到中文界面操作控件：" + operation["tap"], "steps": steps, "failed_step": "zh-CN-operation"}
            elif has(owned(before), operation["expect"]):
                result = {"result": "insufficient", "message": "中文操作前已出现预期结果，无法证明点击生效", "steps": steps,
                          "failed_step": "zh-CN-operation"}
            else:
                bounds = [int(value) for value in re.findall(r"\d+", target.get("bounds", ""))]
                if len(bounds) != 4 or bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
                    result = {"result": "insufficient", "message": "中文操作控件无有效边界", "steps": steps, "failed_step": "zh-CN-operation"}
                else:
                    shell(serial, "input", "tap", str((bounds[0] + bounds[2]) // 2), str((bounds[1] + bounds[3]) // 2))
                    time.sleep(.45)
                    after = snapshot("zh-CN-after-operation")
                    matched = has(owned(after), operation["expect"])
                    steps.append({"phase": "zh-CN-operation", "tap": operation["tap"], "expect": operation["expect"], "matched": matched})
                    if not matched:
                        result = {"result": "failed", "message": "中文界面操作未得到预期结果：" + operation["expect"], "steps": steps, "failed_step": "zh-CN-operation"}
        if result is None:
            result = phase("en-US-return", "en-US", "original")
        if result is None:
            result = {"result": "passed", "message": "英语、简体中文及英语回退均已验证", "steps": steps, "evidence_folder": dest.name}
    except (RuntimeError, ValueError) as exc:
        result = {"result": "insufficient", "message": str(exc), "steps": steps}
    finally:
        try:
            if read_original:
                set_locales(original)
                restored = get_locales() == original
        except (RuntimeError, ValueError):
            restored = False
    result["original_app_locales"] = list(original)
    result["restored"] = restored
    if not restored and result["result"] == "passed":
        result.update(result="insufficient", message="已完成文字检查，但未能恢复应用原语言")
    return result
