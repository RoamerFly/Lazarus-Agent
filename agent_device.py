"""Model-visible operations on the workbench's existing disposable device."""
import re
import subprocess
import time
import xml.etree.ElementTree as ET

import bench_device
from agent_runtime import java_crashes
from diagnose import inspect_package
from function_probe import resolve_platform_library


OBSERVATION_ERRORS = (OSError, ValueError, RuntimeError, subprocess.SubprocessError)


class AgentDevice:
    def __init__(self, device, owner, api, tools, progress):
        self.device, self.owner, self.api, self.tools = device, owner, api, tools
        self.progress = progress
        self.facts = None
        self.serial = None
        self.steps = []

    def test(self, artifact, expected_hash, *, capture_ui=True):
        self.facts = inspect_package(artifact, self.tools)
        if self.facts["sha256"] != expected_hash:
            raise ValueError("设备测试前候选哈希不符")
        self.serial = self.device.ensure(self.owner, self.api, self.progress, self.facts["native_abis"])
        before = self._crashes() if capture_ui else None
        result = self.device.install_run(artifact, self.facts)
        installed = None
        if result["install"]["exit"] == 0:
            lines = bench_device.shell(self.serial, "pm", "path", self.facts["package"]).splitlines()
            if len(lines) != 1 or not re.fullmatch(r"package:/data/app/[\w./=+~-]+/base\.apk", lines[0]):
                raise ValueError("无法回读测试设备安装包")
            installed = bench_device.shell(self.serial, "sha256sum", "'" + lines[0][8:] + "'", timeout=120).split()[0]
            if installed != expected_hash:
                raise ValueError("设备安装包与当前候选哈希不符")
        self.steps = []
        launched = (result.get("launch") or {}).get("exit") == 0
        ui, runtime = self._observe_after(before) if installed and capture_ui else (None, {})
        return {"api": self.api, "environment": "private-workbench-device",
                "install": result["install"], "launch": result.get("launch"),
                "installed_sha256": installed, "abis": result["abis"],
                **runtime, "ui": ui,
                "verification": "install-and-launch" if installed and launched and not runtime else "device-check-failed",
                "steps": self.steps}

    def _crashes(self):
        try:
            self.device.check(self.owner)
            proc = bench_device.invoke([bench_device.ADB, "-s", self.serial, "logcat", "-b", "crash",
                                        "-d", "-v", "threadtime", "-t", "160"], 6)
        except OBSERVATION_ERRORS:
            return None
        if proc.returncode:
            return None
        return java_crashes(proc.stdout.decode("utf-8", "replace"), self.facts["package"])

    def _new_crash(self, before):
        if before is not None:
            after = self._crashes() or {}
            recent = [event for marker, event in after.items() if marker not in before]
            if recent:
                crash = {"source": "isolated-android-crash-buffer", **recent[-1]}
                if crash.get("missing_class"):
                    try:
                        listings = bench_device.shell(self.serial, "pm", "list", "libraries", timeout=6)
                    except OBSERVATION_ERRORS:
                        listings = ""
                    if library := resolve_platform_library(crash["missing_class"], listings):
                        crash["platform_library"] = {"name": library, "evidence": "listed-by-target-package-manager"}
                return {"runtime_crash": crash}
        return {}

    def _observe_after(self, before):
        try:
            ui = self.observe()
        except OBSERVATION_ERRORS:
            runtime = self._new_crash(before)
            if not runtime:
                raise
            return None, runtime
        return ui, self._new_crash(before)

    def tree(self):
        self.device.check(self.owner)
        path = "/data/local/tmp/lazarus-agent-ui.xml"
        bench_device.shell(self.serial, "rm", "-f", path)
        bench_device.shell(self.serial, "uiautomator", "dump", path, timeout=25)
        xml = bench_device.shell(self.serial, "cat", path)
        if len(xml) > 512*1024:
            raise ValueError("设备界面树过大")
        try:
            return ET.fromstring(xml)
        except ET.ParseError as exc:
            raise ValueError("设备返回的界面树无效") from exc

    def window_context(self):
        self.device.check(self.owner)
        windows = bench_device.shell(self.serial, "dumpsys", "window", "displays", timeout=12)
        markers = ("mCurrentFocus=", "mFocusedApp=", "mShowingLockscreen=", "mObscuringWindow=")
        return {"display_fields": [line.strip()[:280] for line in windows.splitlines()
                                  if line.strip().startswith(markers)][:12]}

    def observe(self):
        tree = self.tree()
        nodes = [{key: node.get(key, "")[:300] for key in
                  ("text", "content-desc", "resource-id", "class", "bounds", "clickable", "checked", "package")}
                 for node in tree.iter("node") if node.get("text") or node.get("resource-id") or node.get("clickable")=="true"]
        return {"nodes": nodes[:60], "truncated": len(nodes)>60}

    def action(self, action, text=""):
        if len(self.steps) >= 20:
            raise ValueError("本轮设备操作次数已达上限")
        self.device.check(self.owner)
        before = self._crashes() if action != "read" else None
        if action == "read":
            pass
        elif action == "cold_start":
            package = self.facts["package"]
            bench_device.shell(self.serial, "am", "force-stop", package)
            bench_device.shell(self.serial, "am", "start", "-W", "-n", package+"/"+self.facts["launcher"])
        elif action == "back":
            bench_device.shell(self.serial, "input", "keyevent", "4")
        elif action == "input_text" and isinstance(text, str) and re.fullmatch(r"[A-Za-z0-9_ .-]{1,100}", text):
            bench_device.shell(self.serial, "input", "text", text.replace(" ", "%s"))
        elif action == "tap_text" and isinstance(text, str) and 0<len(text)<=200:
            tree = self.tree()
            matches = [node for node in tree.iter("node") if text in
                       (node.get("text"), node.get("content-desc"), node.get("resource-id"))]
            if len(matches) != 1:
                raise ValueError("控件必须唯一匹配当前界面文本、描述或资源 ID")
            bounds = matches[0].get("bounds", "")
            if not re.fullmatch(r"\[\d+,\d+\]\[\d+,\d+\]", bounds):
                raise ValueError("控件位置无效")
            x1,y1,x2,y2 = map(int, re.findall(r"\d+", bounds))
            bench_device.shell(self.serial, "input", "tap", str((x1+x2)//2), str((y1+y2)//2))
        else:
            raise ValueError("设备操作或输入格式不受支持")
        time.sleep(.6)
        ui, runtime = self._observe_after(before)
        result = {"action": action, "text": text, **runtime, "ui": ui}
        self.steps.append(result)
        return result
