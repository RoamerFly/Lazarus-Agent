"""Drive the public modification API against a team-owned APK fixture.

This is an API contract test. It imports the input APK through the same
endpoint used by the desktop product, saves a reviewed plan, starts the worker,
and verifies the stored baseline, no-op rebuild control, candidate and target
APK hashes. UI operations and cold-start snapshots are produced by
``modification_probe.verify_behavior`` inside the normal worker.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
import uuid


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


SPECS = {
    "reader": {
        "apk": ROOT / "fixtures/file-reader/file-reader-forgets-selection.apk",
        "request": "记住上次打开的文件，重新打开后恢复最后选择的文件",
        "criteria": {"selects": [
            {"tap": "Open record B", "expect": "Report B: South sensor calibration"},
            {"tap": "Open record A", "expect": "Report A: North sensor baseline"},
        ]},
    },
    "vault": {
        "apk": ROOT / "fixtures/file-reader-variant/memo-vault-forgets-selection.apk",
        "request": "记住最后打开的文件，强制停止后恢复该文件",
        "criteria": {"selects": [
            {"tap": "Review west memo", "expect": "Memo West: harbour maintenance notes"},
            {"tap": "Review east memo", "expect": "Memo East: greenhouse supply schedule"},
        ]},
    },
}


def locale_case(folder, filename, strings, operation):
    return {
        "apk": ROOT / "fixtures" / folder / filename,
        "kind": "localize-zh",
        "request": "把这款工具汉化，保留英语并验证原来的按钮仍能使用",
        "criteria": {
            "translations": dict(strings),
            "checks": [{"original": en, "translated": zh} for en, zh in strings[:2]],
            "operation": {"tap": strings[1][1], "expect": operation},
        },
    }


SPECS.update({
    "pocket-list": locale_case("pocket-list", "pocket-list-en.apk",
        [("Shopping list", "购物清单"), ("Add eggs", "添加鸡蛋"), ("Eggs added", "已添加鸡蛋")], "已添加鸡蛋"),
    "tea-timer": locale_case("tea-timer", "tea-timer-en.apk",
        [("Tea timer", "泡茶计时"), ("Start timer", "开始计时"), ("Brew %1$d started", "第 %1$d 泡已开始")], "第 1 泡已开始"),
})


def _wait(client, job: str, timeout: int) -> dict:
    deadline = time.monotonic() + timeout
    state = client.get("/api/jobs/" + job).json()
    while state["status"] == "running" and time.monotonic() < deadline:
        time.sleep(1)
        state = client.get("/api/jobs/" + job).json()
    if state["status"] == "running":
        raise TimeoutError("修改工作超过 %d 秒仍未完成" % timeout)
    return state


def run_case(
    name: str,
    api: int,
    timeout: int,
    output: Path | None,
    storage_base: Path,
    lab: Path | None,
    targets: list[int] | None = None,
) -> dict:
    spec = SPECS[name]
    if not spec["apk"].is_file():
        raise FileNotFoundError("先运行 tools/build_file_reader_fixture.py --variant " + name)
    # Configure before importing workbench because its task storage is created
    # during module import. Keep the API job, screenshots, XML and candidate
    # under build/validation-0.4 for review rather than deleting evidence.
    storage = storage_base.resolve() / (name + "-" + uuid.uuid4().hex[:12])
    storage.mkdir(parents=True, exist_ok=False)
    os.environ["LAZARUS_STORAGE_ROOT"] = str(storage)
    if lab is not None:
        os.environ["REVIVER_LAB"] = str(lab.resolve())
    os.environ.setdefault("REVIVER_TOOL_TEMP", r"C:\Temp")
    import workbench
    from fastapi.testclient import TestClient

    client = TestClient(workbench.app, headers={"host": "127.0.0.1:%d" % workbench.PORT})
    token = client.get("/api/config").json()["token"]
    headers = {"x-reviver-token": token}
    requested_targets = sorted(set(targets or [api]))
    if not requested_targets or any(target < 21 for target in requested_targets):
        raise ValueError("targets 必须是 Android API 21 或以上的整数")
    # The imported device/API is the highest target. The product then performs
    # its compatibility matrix through the reviewed plan, rather than this
    # harness bypassing the worker with a separate ADB loop.
    import_api = max(requested_targets)
    imported = client.post("/api/import/%d" % import_api, content=spec["apk"].read_bytes(), headers=headers)
    imported.raise_for_status()
    job = imported.json()["id"]
    kind = spec.get("kind", "remember-file")
    if kind == "localize-zh":
        response = client.post("/api/jobs/%s/modify/resources" % job, json={"base_version": None}, headers=headers)
        response.raise_for_status()
        offered = {row["original"] for row in response.json()["strings"]}
        if not spec["criteria"]["translations"].keys() <= offered:
            raise AssertionError("资源预览未返回样本中的待翻译字符串")
    planned = client.post("/api/jobs/%s/modify/plan" % job, json={
        "kind": "auto", "request": spec["request"], "criteria": spec["criteria"], "targets": requested_targets,
    }, headers=headers)
    planned.raise_for_status()
    plan = planned.json()["plan"]
    assert plan["kind"] == kind, "自动模式未选择预期改造工具"
    started = client.post("/api/jobs/%s/modify/run" % job, json={"plan_id": plan["id"]}, headers=headers)
    started.raise_for_status()
    state = _wait(client, job, timeout)
    modification = state.get("modification", {})
    run = modification.get("runs", [{}])[-1]
    (storage / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if state["status"] != "ready" or run.get("status") != "passed":
        raise AssertionError("API 改造未通过：" + json.dumps({"status": state["status"], "run": run}, ensure_ascii=False))
    baseline = run.get("baseline", {})
    control = run.get("control", {})
    exported = state.get("export", {})
    if baseline.get("result") != "failed" or control.get("result") != "failed":
        raise AssertionError("原包与无修改回编均需复现失败")
    if kind == "remember-file" and (baseline.get("failed_step"), control.get("failed_step")) != (1, 1):
        raise AssertionError("原包和对照应在选择 B 后冷启动丢失")
    if kind == "localize-zh" and any(test.get("failed_locale") != "zh-CN" for test in (baseline, control)):
        raise AssertionError("原包和对照应在简体中文界面验收时失败")
    if not exported or exported.get("sha256") != exported.get("installed_sha256"):
        raise AssertionError("导出 APK 与设备安装 APK 哈希未匹配")
    matrix = state.get("compatibility", {}).get("rows", [])
    if not matrix or any(row.get("status") != "passed" for row in matrix):
        raise AssertionError("目标 Android 版本没有全部通过：" + json.dumps(matrix, ensure_ascii=False))
    report = {
        "case": name, "job": job, "api": import_api, "targets": requested_targets,
        "storage": str(storage), "plan": plan,
        "baseline": baseline, "control": control, "run": run,
        "export": exported, "matrix": matrix,
    }
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=sorted(SPECS), default="reader")
    parser.add_argument("--api", type=int, default=36)
    parser.add_argument("--targets", type=int, nargs="+", help="兼容矩阵目标；省略时仅验证 --api")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--storage-base", type=Path, default=ROOT / "build/validation-0.4")
    parser.add_argument("--lab", type=Path, help="可选的隔离 AVD 工作目录，会写入 REVIVER_LAB")
    args = parser.parse_args()
    try:
        report = run_case(args.case, args.api, args.timeout, args.out, args.storage_base, args.lab, args.targets)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        workbench = sys.modules.get("workbench")
        if workbench is not None and workbench.device.owner:
            workbench.device.stop()
