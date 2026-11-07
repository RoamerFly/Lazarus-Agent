"""Replay user-owned behavior checks against independently installed APKs."""
import re
import subprocess
import time
from collections import Counter

import bench_device
from agent_workspace import digest_file
from android_dialog import system_dialog


OPERANDS = {"tap": "value", "expect": "value", "absent": "value", "input": "value",
            "replace_input": "value", "back": None, "cold_start": None}
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
            if action in {"input", "replace_input"} and not re.fullmatch(r"[A-Za-z0-9_ .-]{1,100}", value):
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


def _read(tester, handled, observations):
    # The two recognized system dialogs can each be handled once per install.
    for _ in range(3):
        tree = tester.tree()
        nodes = _snapshot(tree, tester.facts["package"])
        dialog, button, title = system_dialog(tree)
        frame = {"target_nodes": len(nodes)}
        if not nodes or dialog:
            owners = Counter(n.get("package")[:160] for n in tree.iter("node") if n.get("package"))
            frame["window_owners"] = dict(owners.most_common(8))
        observations.append(frame)
        if not dialog:
            return nodes
        frame["system_dialog"] = dialog
        if title:
            frame["dialog_title"] = title
        if not button or dialog in handled:
            return []
        handled.add(dialog)
        _tap(tester, button["bounds"])
        frame["dialog_action"] = "wait" if dialog == "system-ui-anr" else "acknowledge"
    return []


def _wait_for_launch_ui(tester, row, handled):
    # am start -W can finish before the first accessibility frame appears on
    # a newly booted, busy emulator. Observe again without relaunching the app
    # or accepting a successful command as proof that its UI was displayed.
    deadline = time.monotonic() + 20
    observations = row["launch_observations"] = []
    for attempt in range(8):
        try:
            if _read(tester, handled, observations):
                return True
            if observations[-1].get("system_dialog") == "other-anr":
                break
        except DEVICE_ERRORS as exc:
            observations.append({"error": str(exc)[-500:]})
        if attempt == 7 or time.monotonic() >= deadline:
            break
        time.sleep(1.5)
    try:
        row["launch_window"] = tester.window_context()
    except DEVICE_ERRORS as exc:
        row["launch_window"] = {"error": str(exc)[-500:]}
    return False


def _step(tester, step, row, handled):
    action, value = step["action"], step.get("value")
    tester.device.check(tester.owner)
    if action in {"tap", "expect", "absent", "input", "replace_input", "back"}:
        frames = []
        try:
            nodes = _read(tester, handled, frames)
        finally:
            if len(frames) != 1 or any(not f["target_nodes"] or "system_dialog" in f for f in frames):
                row["window_observations"] = frames
        if not nodes:
            raise ValueError("当前页面未观察到目标应用控件")
    if action in {"tap", "expect", "absent"}:
        matches = _matches(nodes, value)
        row.update(matches=matches[:8], match_count=len(matches), observed=nodes[:24])
        if action == "tap":
            if len(matches) != 1:
                raise ValueError("点击控件必须在目标应用页面唯一匹配")
            _tap(tester, matches[0]["bounds"])
        elif bool(matches) != (action == "expect"):
            raise ValueError("未观察到预期界面状态")
    elif action == "input":
        bench_device.shell(tester.serial, "input", "text", value.replace(" ", "%s"))
    elif action == "replace_input":
        row["field"] = tester.replace_input(value)
    elif action == "back":
        bench_device.shell(tester.serial, "input", "keyevent", "4")
    else:
        package, launcher = tester.facts["package"], tester.facts["launcher"]
        bench_device.shell(tester.serial, "am", "force-stop", package)
        launch = bench_device.shell(tester.serial, "am", "start", "-W", "-n", f"{package}/{launcher}")
        if not re.search(r"(?m)^Status: ok\s*$", launch):
            raise ValueError("冷启动未成功")
        if not _wait_for_launch_ui(tester, row, handled):
            raise ValueError("冷启动返回成功，但未观察到目标应用页面")


def _observe(tester, apk, expected_hash, steps, *, capture_runtime=False):
    row = {"sha256": expected_hash, "build": "not-checked", "install": "not-run",
           "launch": "not-run", "function": "not-run", "steps": []}
    crash_baseline = None
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
        if capture_runtime:
            crash_baseline = tester.crash_snapshot()
        handled = set()
        if not _wait_for_launch_ui(tester, row, handled):
            row["launch"] = "failed"
            raise ValueError("启动返回成功，但未观察到目标应用页面")
        row["launch"] = "passed"
        for step in steps:
            entry = {**step, "status": "failed"}
            row["steps"].append(entry)
            _step(tester, step, entry, handled)
            entry["status"] = "passed"
        row["function"] = "passed" if steps else "not-requested"
    except DEVICE_ERRORS as exc:
        row["failure"] = str(exc)[-1500:]
        if row["steps"]:
            row["function"] = "failed"
    finally:
        if crash_baseline is not None and row["function"] != "passed":
            row.update(tester.new_crash(crash_baseline))
    return row


def rehearse_candidate(tester, apk, expected_hash, steps):
    """Summarize a candidate-only replay for the model's current editing round."""
    row = _observe(tester, apk, expected_hash, steps, capture_runtime=True)
    device = row.get("device") or {}
    result = {key: row[key] for key in ("build", "install", "launch", "function")}
    result.update(api=tester.api, candidate_sha256=expected_hash, installed_sha256=device.get("installed_sha256"),
                  verification="candidate-rehearsal", expected_steps=len(steps),
                  passed_steps=sum(step["status"] == "passed" for step in row["steps"]))
    if row.get("failure"):
        result["failure"] = row["failure"]
    if row.get("runtime_crash"):
        result["runtime_crash"] = row["runtime_crash"]
    if result["install"] == "failed":
        result["install_output"] = (device.get("install") or {}).get("output", "")[-800:]
    if result["launch"] == "failed":
        result["launch_output"] = (device.get("launch") or {}).get("output", "")[-800:]
    if row["steps"] and row["steps"][-1]["status"] == "failed":
        failed = row["steps"][-1]
        result["failed_step"] = {key: failed[key] for key in ("action", "value", "match_count") if key in failed}
        result["failed_step"]["index"] = len(row["steps"])
        if failed.get("observed"):
            result["failed_step"]["observed"] = [
                {key: node[key][:160] for key in ("text", "content-desc", "resource-id") if node[key]}
                for node in failed["observed"][:12]
            ]
    return result


def verification_coverage(steps):
    return {"specified_ui_steps": len(steps), "external_app_calls": "not-tested"}


def verify(source, source_hash, candidate, candidate_hash, targets, steps, make_tester, available, progress):
    """Install the same candidate bytes across targets and replay fixed checks."""
    report = {"schema": 1, "targets": targets, "steps": steps, "input_sha256": source_hash,
              "candidate_sha256": candidate_hash, "coverage": verification_coverage(steps), "rows": []}
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


def _verified_step(entry, step):
    if entry["status"] != "passed" or any(entry.get(key) != value for key, value in step.items()):
        return False
    if step["action"] != "replace_input":
        return True
    field = entry.get("field")
    return isinstance(field, dict) and bool(field.get("resource-id")) and field.get("text") == step["value"]


def verified_for(report, sha256):
    if not report or report.get("status") != "behavior-verified" or report.get("candidate_sha256") != sha256:
        return False
    targets, rows, steps = report["targets"], report["rows"], report["steps"]
    return bool(steps and targets and len(targets) == len(rows)) and all(
        row["api"] == api and row["status"] == "behavior-verified" and
        row["candidate"]["device"]["installed_sha256"] == sha256 and
        row["candidate"]["install"] == row["candidate"]["launch"] == row["candidate"]["function"] == "passed" and
        len(row["candidate"]["steps"]) == len(steps) and
        all(_verified_step(entry, step) for entry, step in zip(row["candidate"]["steps"], steps))
        for api, row in zip(targets, rows))
