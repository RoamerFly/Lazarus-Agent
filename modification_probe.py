"""Replay a small, user-visible APK modification acceptance scenario.

The probe only touches the caller's already selected isolated device.  It
stores the UI tree and screenshot for each observable step so that a reported
"behaviour changed" result can be reviewed without relying on model text.
"""
from __future__ import annotations

from pathlib import Path
import re
import time
import xml.etree.ElementTree as ET

from bench_device import ADB, invoke, shell


_PACKAGE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+")
_ACTIVITY = re.compile(r"[A-Za-z0-9_.$]+")
_MAX_STEPS = 8


def _validate(facts: dict, criteria: dict) -> tuple[str, str, list[dict]]:
    if not isinstance(facts, dict) or not isinstance(criteria, dict):
        raise ValueError("验收输入格式无效")
    package = facts.get("package")
    launcher = facts.get("launcher")
    selects = criteria.get("selects")
    if not isinstance(package, str) or not _PACKAGE.fullmatch(package):
        raise ValueError("APK 包名无效")
    if not isinstance(launcher, str) or not _ACTIVITY.fullmatch(launcher):
        raise ValueError("APK 启动 Activity 无效")
    if not isinstance(selects, list) or not 1 <= len(selects) <= _MAX_STEPS:
        raise ValueError("验收步骤需要 1 至 %d 次选择" % _MAX_STEPS)
    checked = []
    for step in selects:
        if not isinstance(step, dict):
            raise ValueError("验收步骤格式无效")
        tap, expect = step.get("tap"), step.get("expect")
        if not all(isinstance(value, str) and 0 < len(value.strip()) <= 120
                   for value in (tap, expect)):
            raise ValueError("每步均需提供不超过 120 字的控件和预期内容")
        checked.append({"tap": tap.strip(), "expect": expect.strip()})
    return package, launcher, checked


def _safe_label(index: int, phase: str) -> str:
    return "step-%02d-%s" % (index, phase)


def verify_behavior(serial: str, facts: dict, criteria: dict, dest: Path) -> dict:
    """Verify that each chosen item remains selected after a cold start.

    ``criteria`` is ``{"selects": [{"tap": "Record B", "expect": "B"},
    ...]}``.  The function returns a JSON-serialisable result with an evidence
    folder relative to the job.  It never considers an unavailable control a
    pass, which separates a missing UI from an app that forgot the selection.
    """
    package, launcher, selects = _validate(facts, criteria)
    if not isinstance(serial, str) or not serial:
        raise ValueError("测试设备无效")
    dest.mkdir(parents=True, exist_ok=False)
    snapshots: list[dict] = []

    def snapshot(label: str) -> ET.Element:
        remote = "/data/local/tmp/lazarus-modification-ui.xml"
        # A failed dump must never be replaced with an earlier UI tree from a
        # previous assertion on this device.
        shell(serial, "rm", "-f", remote)
        shell(serial, "uiautomator", "dump", remote)
        xml = invoke([ADB, "-s", serial, "exec-out", "cat", remote]).stdout
        if not xml:
            raise RuntimeError("无法读取设备界面树")
        (dest / (label + ".xml")).write_bytes(xml)
        png = invoke([ADB, "-s", serial, "exec-out", "screencap", "-p"]).stdout
        if png.startswith(b"\x89PNG"):
            (dest / (label + ".png")).write_bytes(png)
        try:
            return ET.fromstring(xml)
        except ET.ParseError as exc:
            raise RuntimeError("设备返回的界面树无效") from exc

    def owned_match(tree: ET.Element, value: str):
        return next((node for node in tree.iter("node")
                     if node.get("package") == package and value in {
                         node.get("text"), node.get("content-desc"), node.get("resource-id")}), None)

    def tap(node: ET.Element) -> None:
        values = [int(value) for value in re.findall(r"\d+", node.get("bounds", ""))]
        if len(values) != 4 or values[0] >= values[2] or values[1] >= values[3]:
            raise RuntimeError("目标控件没有可点击的界面边界")
        shell(serial, "input", "tap", str((values[0] + values[2]) // 2),
              str((values[1] + values[3]) // 2))

    def cold_start() -> bool:
        shell(serial, "am", "force-stop", package)
        result = invoke([ADB, "-s", serial, "shell", "am", "start", "-W", "-n",
                         package + "/" + launcher], 30)
        if result.returncode:
            raise RuntimeError("冷启动失败：" + (result.stderr + result.stdout).decode("utf-8", "replace")[-300:])
        time.sleep(0.6)
        windows = invoke([ADB, "-s", serial, "shell", "dumpsys", "window", "displays"], 15)
        text = (windows.stdout + windows.stderr).decode("utf-8", "replace")
        focus = re.search(r"(?m)^\s*mCurrentFocus=Window\{[^\r\n}]*?\bu\d+\s+([\w.]+)/", text)
        return bool(focus and focus.group(1) == package)

    def visible_owned_nodes(tree: ET.Element) -> bool:
        return any(node.get("package") == package for node in tree.iter("node"))

    def owned_signature(tree: ET.Element) -> tuple[tuple[str | None, str | None, str | None], ...]:
        return tuple(sorted((node.get("text",""), node.get("content-desc",""), node.get("resource-id",""))
                            for node in tree.iter("node") if node.get("package") == package))

    for index, step in enumerate(selects, 1):
        before = snapshot(_safe_label(index, "before-select"))
        target = owned_match(before, step["tap"])
        if target is None:
            return {"result": "insufficient", "message": "找不到应用内选择控件：" + step["tap"],
                    "steps": snapshots, "failed_step": index}
        tap(target)
        time.sleep(0.45)
        after = snapshot(_safe_label(index, "after-select"))
        if not visible_owned_nodes(after):
            return {"result": "insufficient", "message": "点击后目标应用不在前台，无法确认控件是否生效",
                    "steps": snapshots, "failed_step": index}
        if owned_match(after, step["expect"]) is None:
            if owned_signature(after) == owned_signature(before):
                return {"result": "insufficient", "message": "点击后应用界面没有变化，无法确认控件操作是否执行",
                        "steps": snapshots, "failed_step": index}
            snapshots.append({"step": index, "tap": step["tap"], "expect": step["expect"],
                              "selected": False, "cold_start": None})
            return {"result": "failed", "message": "选择后未显示预期内容：" + step["expect"],
                    "steps": snapshots, "failed_step": index}
        previous = [entry["expect"] for entry in snapshots]
        if any(owned_match(after, old) is not None for old in previous):
            snapshots.append({"step": index, "tap": step["tap"], "expect": step["expect"],
                              "selected": False, "cold_start": None})
            return {"result": "failed", "message": "选择后仍显示此前记录，未观察到状态切换",
                    "steps": snapshots, "failed_step": index}
        foreground = cold_start()
        restored = snapshot(_safe_label(index, "after-cold-start"))
        if not foreground or not visible_owned_nodes(restored):
            snapshots.append({"step": index, "tap": step["tap"], "expect": step["expect"],
                              "selected": True, "cold_start": None})
            return {"result": "insufficient", "message": "冷启动后目标应用不在前台，无法判断是否恢复",
                    "steps": snapshots, "failed_step": index}
        kept = owned_match(restored, step["expect"]) is not None
        snapshots.append({"step": index, "tap": step["tap"], "expect": step["expect"],
                          "selected": True, "cold_start": kept})
        if not kept:
            return {"result": "failed", "message": "冷启动后没有恢复所选内容：" + step["expect"],
                    "steps": snapshots, "failed_step": index}
    return {"result": "passed", "message": "所有选中内容均在冷启动后恢复",
            "steps": snapshots, "evidence_folder": dest.name}
