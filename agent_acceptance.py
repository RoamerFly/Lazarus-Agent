"""Replay user-owned behavior checks against independently installed APKs."""
import re
import subprocess
import time

import bench_device
from agent_workspace import digest_file


OPERANDS = {"tap": "value", "expect": "value", "absent": "value", "input": "value",
            "back": None, "cold_start": None}
DEVICE_ERRORS = (ValueError, OSError, RuntimeError, subprocess.SubprocessError)


def parse_steps(task):
    """Normalize the existing two-field UI and the replay API to one plan."""
    if task is None:
        return []
    if isinstance(task, dict) and set(task) == {"tap", "expect"}:
        task = {"steps": [{"action": key, "value": task[key]} for key in ("tap", "expect")]}
    if not isinstance(task, dict) or set(task) != {"steps"}:
        raise ValueError("验收任务需要点击与预期，或按顺序填写 steps")
    steps = task["steps"]
    if not isinstance(steps, list) or not 1 <= len(steps) <= 16:
        raise ValueError("验收步骤需要 1 至 16 项")
    result = []
    for step in steps:
        action = step.get("action") if isinstance(step, dict) else None
        if not isinstance(action, str) or action not in OPERANDS:
            raise ValueError("不支持的验收操作")
        operand = OPERANDS[action]
        if set(step) != ({"action", operand} if operand else {"action"}):
            raise ValueError("验收步骤字段不完整或含有额外字段")
        if operand:
            value = step[operand]
            if not isinstance(value, str) or not 0 < len(value.strip()) <= 200 or "\0" in value:
                raise ValueError("验收内容需要 1 至 200 字")
            if action == "input" and not re.fullmatch(r"[A-Za-z0-9_ .-]{1,100}", value):
                raise ValueError("验收输入仅支持英文、数字、空格和 _ . -")
        result.append(dict(step))
    if not any(step["action"] == "expect" for step in result):
        raise ValueError("验收需要至少一个可观察的预期结果")
    return result


def _snapshot(tree, package):
    fields = ("text", "content-desc", "resource-id", "bounds")
    return [{key: node.get(key, "")[:400] for key in fields}
            for node in tree.iter("node") if node.get("package") == package]


def _matches(nodes, value):
    return [n for n in nodes if value in (n["text"], n["content-desc"], n["resource-id"])]


def _tap(tester, bounds):
    match = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds)
    if match is None:
        raise ValueError("控件坐标不可读")
    x1, y1, x2, y2 = map(int, match.groups())
    if x1 >= x2 or y1 >= y2:
        raise ValueError("控件区域为空")
    bench_device.shell(tester.serial, "input", "tap", str((x1+x2)//2), str((y1+y2)//2))
    time.sleep(.6)


def _read(tester):
    tree = tester.tree()
    nodes = _snapshot(tree, tester.facts["package"])
    if nodes:
        return nodes
    # Only dismiss the Android system's legacy-target notice. An app's
    # onboarding or consent stays part of the explicitly supplied replay.
    system = _snapshot(tree, "android")
    notice = next((n for n in system if n["resource-id"] == "android:id/message"), None)
    ok = [n for n in system if n["resource-id"] == "android:id/button1" and n["text"] == "OK"]
    if notice and len(ok) == 1 and notice["text"].startswith((
            "This app was built for an older version of Android",
            "This app isn't compatible with the latest version of Android.")):
        _tap(tester, ok[0]["bounds"])
        return _snapshot(tester.tree(), tester.facts["package"])
    return nodes


def _step(tester, step, row):
    action, value = step["action"], step.get("value")
    tester.device.check(tester.owner)
    if action in {"tap", "expect", "absent"}:
        nodes = _read(tester)
        if not nodes:
            raise ValueError("当前页面未观察到目标应用控件")
        matches = _matches(nodes, value)
        row.update(matches=matches[:8], match_count=len(matches), observed=nodes[:24])
        if action == "tap":
            if len(matches) != 1:
                raise ValueError("点击控件必须在目标应用页面唯一匹配")
            _tap(tester, matches[0]["bounds"])
        elif bool(matches) != (action == "expect"):
            raise ValueError("未观察到预期界面状态")
    elif action == "input":
        if not _read(tester):
            raise ValueError("当前页面未观察到目标应用控件")
        bench_device.shell(tester.serial, "input", "text", value.replace(" ", "%s"))
    elif action == "back":
        if not _read(tester):
            raise ValueError("当前页面未观察到目标应用控件")
        bench_device.shell(tester.serial, "input", "keyevent", "4")
    else:
        package, launcher = tester.facts["package"], tester.facts["launcher"]
        bench_device.shell(tester.serial, "am", "force-stop", package)
        launch = bench_device.shell(tester.serial, "am", "start", "-W", "-n", f"{package}/{launcher}")
        if not re.search(r"(?m)^Status: ok\s*$", launch):
            raise ValueError("冷启动未成功")


def _observe(tester, apk, expected_hash, steps):
    row = {"sha256": expected_hash, "build": "not-checked", "install": "not-run",
           "launch": "not-run", "function": "not-run", "steps": []}
    try:
        if digest_file(apk) != expected_hash:
            raise ValueError("APK 文件哈希与本轮记录不一致")
        row["build"] = "hash-matched"
        proof = tester.test(apk, expected_hash, capture_ui=False)
        row["device"] = proof
        if proof["install"]["exit"] != 0 or proof["installed_sha256"] != expected_hash:
            row["install"] = "failed"
            return row
        row["install"] = "passed"
        launch = proof.get("launch") or {}
        if launch.get("exit") != 0 or not re.search(r"(?m)^Status: ok\s*$", launch.get("output", "")):
            row["launch"] = "failed"
            return row
        if not _read(tester):
            row["launch"] = "failed"
            raise ValueError("启动返回成功，但未观察到目标应用页面")
        row["launch"] = "passed"
        for step in steps:
            entry = {**step, "status": "failed"}
            row["steps"].append(entry)
            _step(tester, step, entry)
            entry["status"] = "passed"
        row["function"] = "passed" if steps else "not-requested"
    except DEVICE_ERRORS as exc:
        row["failure"] = str(exc)[-1500:]
        if row["steps"]:
            row["function"] = "failed"
    return row


def verify(source, source_hash, candidate, candidate_hash, targets, steps, make_tester, available, progress):
    """Install the same candidate bytes across targets and replay fixed checks."""
    report = {"schema": 1, "targets": targets, "steps": steps, "input_sha256": source_hash,
              "candidate_sha256": candidate_hash, "rows": []}
    for api in targets:
        if not available(api):
            report["rows"].append({"api": api, "status": "unavailable"})
            continue
        progress(f"独立验收 Android {api - 20}，核对原包与候选")
        tester = make_tester(api)
        baseline = _observe(tester, source, source_hash, steps) if steps else None
        proof = _observe(tester, candidate, candidate_hash, steps)
        status = "behavior-verified" if proof["function"] == "passed" else (
            "launch-verified" if proof["function"] == "not-requested" else "failed")
        if baseline is None:
            comparison = "not-requested"
        elif baseline["function"] == "passed":
            comparison = "baseline-already-passed"
        elif baseline["function"] == "failed":
            comparison = "baseline-check-failed"
        elif baseline["install"] == "failed":
            comparison = "baseline-install-failed"
        else:
            comparison = "baseline-inconclusive"
        report["rows"].append({"api": api, "status": status, "baseline": baseline,
                               "candidate": proof, "comparison": comparison})
    report["status"] = ("behavior-verified" if steps else "launch-verified") if targets and all(
        row["status"] in {"behavior-verified", "launch-verified"} for row in report["rows"]) else "incomplete"
    return report


def verified_for(report, sha256):
    if not report or report.get("status") != "behavior-verified" or report.get("candidate_sha256") != sha256:
        return False
    targets, rows, steps = report["targets"], report["rows"], report["steps"]
    return bool(steps and targets and len(targets) == len(rows)) and all(
        row["api"] == api and row["status"] == "behavior-verified" and
        row["candidate"]["device"]["installed_sha256"] == sha256 and
        row["candidate"]["install"] == row["candidate"]["launch"] == row["candidate"]["function"] == "passed" and
        len(row["candidate"]["steps"]) == len(steps) and
        all(entry["status"] == "passed" and all(entry.get(key) == value for key, value in step.items())
            for entry, step in zip(row["candidate"]["steps"], steps))
        for api, row in zip(targets, rows))
