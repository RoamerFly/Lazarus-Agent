"""Capture the workbench with recorded tool calls and real Android acceptance."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from threading import Thread
import time
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def capture(source, output, port, device_followup=False):
    recorded = json.loads((ROOT / "docs/validation/recorded-edit-fixtures/P01.json").read_text(encoding="utf-8"))
    commit = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if subprocess.check_output(["git", "-C", str(source), "status", "--porcelain"]):
        raise ValueError("使用干净的候选源码检出目录")
    build = json.loads((source / "build/local-exe-selftest-receipt.json").read_text(encoding="utf-8-sig"))
    with (source / "dist/Lazarus-Agent.exe").open("rb") as executable:
        exe_sha256 = hashlib.file_digest(executable, "sha256").hexdigest()
    if (build["source_commit"] != commit or build["self_test_exit"] != 0
            or build["exe_sha256"] != exe_sha256):
        raise ValueError("录制源码必须与候选 EXE 的构建回执一致")
    output.mkdir(parents=True, exist_ok=False)
    for key in tuple(os.environ):
        if key.upper().startswith(("LAZARUS_", "REVIVER_MODEL_")):
            del os.environ[key]
    os.environ.update(LAZARUS_STORAGE_ROOT=str(output / "storage"), LAZARUS_MIGRATE_LEGACY="0",
                      REVIVER_PORT=str(port),
                      REVIVER_MODEL_URL="http://127.0.0.1:1", REVIVER_MODEL="recorded-tools",
                      REVIVER_MODEL_KEY="offline-placeholder")
    sys.path.insert(0, str(source))
    import bench_device
    import feature_agent
    import uvicorn
    import workbench
    from agent_acceptance import verified_for
    from agent_workspace import digest_file
    from app_version import APP_VERSION
    from playwright.sync_api import sync_playwright

    case = next(entry for entry in json.loads((source / "tools/f01-p1c-cases.json").read_text(encoding="utf-8"))["cases"]
                if entry["id"] == recorded["case"])
    original = source / case["apk"]
    if (APP_VERSION != build["version"] or digest_file(original) != recorded["source_sha256"]
            or "emulator-5578" in subprocess.check_output([bench_device.ADB, "devices"], text=True)):
        raise ValueError("原 APK、程序版本或专用模拟器状态不符合录制条件")
    calls = [(row["tool"], row["arguments"]) for row in recorded["tools"]]
    calls.append(("build_apk", {}))
    if device_followup:
        calls.extend([("rehearse_acceptance", {}), ("device_action", {"action": "read"})])
    calls.append(("finish", {"outcome": "candidate", "summary": "已重放留档编辑。候选进入独立设备验收。"}))
    index = 0

    def recorded_reply(_url, payload, _key, *, timeout):
        nonlocal index
        if index:
            last = next(json.loads(message["content"]) for message in reversed(payload["messages"])
                        if message["role"] == "tool")
            if not last["ok"]:
                raise ValueError(f"留档工具未重放成功 {calls[index - 1][0]}")
        tool, arguments = calls[index]
        index += 1
        return {"tool_calls": [{"id": f"recorded-{index}", "function": {"name": tool,
                                "arguments": json.dumps(arguments, ensure_ascii=False)}}]}

    def save(name, data):
        (output / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    host = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(uvicorn.Config(workbench.app, host="127.0.0.1", port=port, log_level="error"))
    thread = Thread(target=server.run, daemon=True)
    thread.start()
    frames = []
    try:
        with patch.object(feature_agent, "_request", side_effect=recorded_reply), sync_playwright() as playwright:
            deadline = time.monotonic() + 30
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    raise TimeoutError("工作台启动失败")
                time.sleep(.1)
            browser = playwright.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))

            def frame(label):
                name = f"screen-{len(frames):04}.png"
                page.screenshot(path=output / name, animations="disabled")
                frames.append({"file": name, "label": label, "sha256": digest_file(output / name)})
                save("frames.json", frames)
                print(label, flush=True)

            page.goto(host)
            page.wait_for_function('!document.getElementById("file").disabled')
            frame("打开工作台")
            page.locator("#scopeSummary").click()
            page.locator("#target").select_option("36")
            page.keyboard.press("Escape")
            page.locator("#file").set_input_files(original)
            page.wait_for_function('!document.getElementById("sendMessage").disabled')
            frame("导入离线巡检单并选择 Android 16")
            page.locator('[data-open-dialog="taskDialog"]').click()
            page.locator("#taskDialog details summary").click()
            labels = {"expect": "出现", "tap": "点击", "cold_start": "冷启动"}
            page.locator("#acceptanceSteps").fill("\n".join(" | ".join([labels[step[0]], *step[1:]]) for step in case["steps"]))
            frame("提交前固定路线切换和冷启动步骤")
            page.keyboard.press("Escape")
            page.locator("#message").fill(case["request"])
            frame("在修复对话提出需求")
            with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith("/message")) as event:
                page.locator("#sendMessage").click()
            submission = event.value.json()
            if submission["action"] != "chat-feature" or submission["api"] != 36:
                raise ValueError("工作台未进入目标对话修改入口")
            job = submission["id"]
            last = 0
            deadline = time.monotonic() + 900
            while time.monotonic() < deadline:
                state = workbench.read(workbench.STORAGE / job)
                messages = state.get("messages", [])
                if state["status"] != "running":
                    break
                if len(messages) > last:
                    page.wait_for_timeout(550)
                    frame(messages[-1]["text"][:100])
                    last = len(messages)
                page.wait_for_timeout(650)
            else:
                raise TimeoutError("工作台录制超时")
            page.wait_for_function('!document.getElementById("sendMessage").disabled')
            page.wait_for_function('!document.getElementById("notice").textContent.includes("正在处理")')
            frame("最终对话结果与独立验收")
            candidate = state["feature_candidates"][state["latest_feature_candidate"]]
            report = candidate["acceptance"]
            if (errors or index != len(calls) or candidate["changes"] != recorded["changes"]
                    or report["targets"] != [36] or not verified_for(report, candidate["sha256"])):
                raise ValueError("录制的编辑、工作台或独立设备验收未通过")
            if device_followup:
                trace_path = (workbench.STORAGE / job / "feature-candidates"
                              / state["latest_feature_candidate"] / "agent-trace.json")
                tool_calls = json.loads(trace_path.read_text(encoding="utf-8"))["tools"]
                rehearsal, followup = tool_calls[-3:-1]
                if (rehearsal["tool"] != "rehearse_acceptance" or not rehearsal["ok"]
                        or rehearsal["result"]["function"] != "passed"
                        or followup["tool"] != "device_action" or not followup["ok"]
                        or followup["result"]["tested_input"] != "candidate"):
                    raise ValueError("录制未验证预检后的候选前台读取")
            with page.expect_download() as download:
                page.get_by_role("link", name="下载已验收 APK").last.click()
            download.value.save_as(output / "candidate.apk")
            if digest_file(output / "candidate.apk") != candidate["sha256"]:
                raise ValueError("工作台下载 APK 与设备验收不一致")
            save("agent-acceptance.json", report)

            workbench.device.check(job)
            serial, package = workbench.device.serial, state["package"]["package"]
            if bench_device.name_at(serial) != "ReviverBench36":
                raise ValueError("录制使用的模拟器不符")
            paths = bench_device.shell(serial, "pm", "path", package).splitlines()
            if len(paths) != 1 or not re.fullmatch(r"package:/data/app/[A-Za-z0-9_./=+~-]+/base\.apk", paths[0]):
                raise ValueError("无法确认设备安装路径")
            installed = bench_device.shell(serial, "sha256sum", paths[0][8:]).split()[0]
            display = bench_device.shell(serial, "dumpsys", "window", "displays")
            focus = re.search(r"(?m)^\s*mCurrentFocus=Window\{[^\r\n}]*?\bu\d+\s+([\w.]+)/", display)
            shot = bench_device.invoke([bench_device.ADB, "-s", serial, "exec-out", "screencap", "-p"], timeout=12)
            if (installed != candidate["sha256"] or not focus or focus.group(1) != package
                    or shot.returncode or not shot.stdout.startswith(b"\x89PNG\r\n\x1a\n")):
                raise ValueError("设备截图与已验收 APK 不符")
            screenshot = output / "device-final.png"
            screenshot.write_bytes(shot.stdout)
            save("capture.json", {"app_version": APP_VERSION, "source_commit": commit, "exe_sha256": exe_sha256,
                 "mode": "recorded-tools",
                 "network_model_calls": 0, "replayed_tool_calls": index, "job_id": job,
                 "device_followup": device_followup,
                 "candidate_sha256": candidate["sha256"], "input_sha256": candidate["source_sha256"],
                 "trace_sha256": digest_file(ROOT / "docs/validation/recorded-edit-fixtures/P01.json"), "frames": frames,
                 "device_screen": {"file": screenshot.name, "sha256": digest_file(screenshot), "apk_sha256": installed,
                                   "api": 36, "package": package}})
            browser.close()
    finally:
        with workbench.operation:
            workbench.device.stop()
            server.should_exit = True
        thread.join(timeout=15)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--port", type=int, default=18825)
    parser.add_argument("--device-followup", action="store_true")
    args = parser.parse_args()
    capture(args.source.resolve(), args.output.resolve(), args.port, args.device_followup)
