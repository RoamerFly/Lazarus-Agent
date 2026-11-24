"""Fast, explicitly selected emulator/USB previews and non-destructive phone runs."""
from pathlib import Path
import re
import subprocess
import struct
import os
import threading
import time

import bench_device as bench


def usb_devices():
    result = bench.invoke([bench.ADB, "devices", "-l"], 8)
    if result.returncode:
        raise ValueError("无法读取 ADB 设备列表")
    devices = []
    for line in result.stdout.decode("utf-8", "replace").splitlines()[1:]:
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, status = parts[:2]
        if serial.startswith("emulator-") or ":" in serial or not re.fullmatch(r"[\w.-]{1,128}", serial):
            continue
        model = next((p[6:].replace("_", " ") for p in parts[2:] if p.startswith("model:")), "Android 手机")
        devices.append({"id": serial, "label": "USB · " + model,
                        "state": status, "available": status == "device"})
    return devices


def runtime_evidence(serial, package):
    """Read only the imported package's PID-scoped logs; never clear phone logs."""
    if not re.fullmatch(r"[A-Za-z0-9_.]+", package):
        raise ValueError("包名无效")
    process = bench.invoke([bench.ADB, "-s", serial, "shell", "pidof", package], 5)
    pids = set(process.stdout.decode("ascii", "ignore").split()) if process.returncode == 0 else set()
    crash = bench.invoke([bench.ADB, "-s", serial, "logcat", "-b", "crash", "-d", "-v", "threadtime", "-t", "300"], 8)
    crash_text = crash.stdout.decode("utf-8", "replace")
    pids.update(re.findall(r"Process:\s*" + re.escape(package) + r"(?:[:][\w.]+)?,\s*PID:\s*(\d+)", crash_text))
    logs = []
    for pid in sorted(p for p in pids if p.isdigit())[-4:]:
        result = bench.invoke([bench.ADB, "-s", serial, "logcat", "-d", "-v", "brief", "--pid", pid, "-t", "160"], 8)
        logs.append(result.stdout.decode("utf-8", "replace")[-6000:])
    return {"package": package, "scope": "package-pids-only", "log": "\n".join(logs)[-12000:],
            "note": "截图、错误提示和日志是待核查证据；未出现日志不代表没有故障。"}


class Preview:
    def __init__(self, emulator):
        self.emulator = emulator
        self.selected = "emulator"
        self.generation = 0
        self._lock = threading.RLock()
        self._frame = None
        self._checked = (None, 0)
        self.last_capture_ms = None
        self._png_only = set()
        self._video = None
        self._video_retry_after = {}
        self._geometry_checked = 0

    def inventory(self):
        return {"selected": self.selected, "generation": self.generation,
                "devices": [{"id": "emulator", "label": "模拟器", "available": True, "state": "device"}] + usb_devices()}

    def select(self, value):
        if value != "emulator" and not any(d["id"] == value and d["available"] for d in usb_devices()):
            raise ValueError("USB 手机未连接或尚未允许 USB 调试，请在手机上确认授权")
        with self._lock:
            if self._video:
                self._video.stop()
                self._video = None
            self.selected = value
            self.generation += 1
            self._frame = None
            self._checked = (None, 0)
            self._video_retry_after.clear()
            if value != "emulator" and os.environ.get("REVIVER_PREVIEW_STREAM") != "0":
                geometry = self._usb_geometry(value)
                if geometry:
                    self._start_video(value, *geometry)
        return {"selected": self.selected, "generation": self.generation}

    def _start_video(self, serial, width, height):
        from usb_video import UsbVideo
        self._video = UsbVideo(serial, width, height)
        self._geometry_checked = time.monotonic()

    def serial(self, owner):
        if self.selected != "emulator":
            return self.selected
        # Do not launch an emulator merely to display a preview.
        key = (owner, self.emulator.serial)
        if self._checked[0] != key or time.monotonic() - self._checked[1] > 3:
            self.emulator.check(owner)
            self._checked = (key, time.monotonic())
        return self.emulator.serial

    def frame(self, owner):
        import cv2
        import numpy as np
        with self._lock:
            serial = self.serial(owner)
            key = (self.generation, owner, serial)
            if self._video:
                video = self._video.latest()
                if video is None and self._frame is None and self.selected != "emulator":
                    # On first selection, give the already-starting encoder a
                    # short head start before paying for a full USB screenshot.
                    deadline = time.monotonic() + .45
                    while video is None and not self._video._stop.is_set() and time.monotonic() < deadline:
                        time.sleep(.02)
                        video = self._video.latest()
                if time.monotonic() - self._geometry_checked > 2:
                    self._geometry_checked = time.monotonic()
                    geometry = self._usb_geometry(serial)
                    if geometry and geometry != (self._video.width, self._video.height):
                        self._video.stop(); self._video = None; self._frame = None; video = None
                if video and self._video:
                    frame = {**video, "generation": self.generation}
                    self._frame = (key, time.monotonic(), frame)
                    return frame
                if self._video and self._video._stop.is_set():
                    if not self._video.frames or self._video.error:
                        # A sleeping phone can refuse screenrecord. Retry after it wakes,
                        # without starting a new encoder on every screenshot request.
                        self._video_retry_after[serial] = time.monotonic() + 5
                    self._video = None
            if self._frame and self._frame[0] == key and time.monotonic() - self._frame[1] < .18:
                return self._frame[2]
            start = time.monotonic()
            image = None
            capture_mode = "raw"
            # A physical phone's raw screencap transfers ~14 MB per frame over USB.
            # Its compressed PNG is substantially faster and is used until H.264 starts.
            if self.selected == "emulator" and serial not in self._png_only:
                result = bench.invoke([bench.ADB, "-s", serial, "exec-out", "screencap"], 8)
                if result.returncode == 0 and len(result.stdout) >= 16:
                    width, height, pixel_format = struct.unpack("<3I", result.stdout[:12])
                    channels = {1: 4, 2: 4, 3: 3, 5: 4}.get(pixel_format)
                    if channels and 0 < width <= 8192 and 0 < height <= 8192:
                        offset = len(result.stdout) - width * height * channels
                        if offset in (12, 16):
                            pixels = np.frombuffer(result.stdout, dtype=np.uint8, offset=offset).reshape(height, width, channels)
                            conversion = {1: cv2.COLOR_RGBA2BGR, 2: cv2.COLOR_RGBA2BGR,
                                          3: cv2.COLOR_RGB2BGR, 5: cv2.COLOR_BGRA2BGR}[pixel_format]
                            image = cv2.cvtColor(pixels, conversion)
            if image is None:
                self._png_only.add(serial)
                capture_mode = "png-usb" if self.selected != "emulator" else "png-fallback"
                result = bench.invoke([bench.ADB, "-s", serial, "exec-out", "screencap", "-p"], 8)
                if result.returncode or not result.stdout.startswith(b"\x89PNG"):
                    raise ValueError("无法取得设备画面，请检查 USB 连接、调试授权或模拟器状态")
                image = cv2.imdecode(np.frombuffer(result.stdout, dtype=np.uint8), cv2.IMREAD_COLOR)
            if image is None:
                raise ValueError("设备画面解码失败")
            height, width = image.shape[:2]
            scale = min(1, 1280 / max(width, height))
            if scale < 1:
                image = cv2.resize(image, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA)
            ok, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 70])
            if not ok:
                raise ValueError("设备画面压缩失败")
            self.last_capture_ms = round((time.monotonic() - start) * 1000)
            frame = {"data": jpeg.tobytes(), "width": width, "height": height,
                     "generation": self.generation, "capture_ms": self.last_capture_ms, "capture_mode": capture_mode}
            self._frame = (key, time.monotonic(), frame)
            if (self.selected != "emulator" and not self._video
                    and time.monotonic() >= self._video_retry_after.get(serial, 0)
                    and os.environ.get("REVIVER_PREVIEW_STREAM") != "0"):
                self._start_video(serial, width, height)
            if self._video:
                video = self._video.latest()
                if video:
                    frame = {**video, "generation": self.generation}
                    self._frame = (key, time.monotonic(), frame)
            return frame

    @staticmethod
    def _usb_geometry(serial):
        result = bench.invoke([bench.ADB, "-s", serial, "shell", "dumpsys", "input"], 5)
        text = result.stdout.decode("utf-8", "replace")
        match = re.search(r"Viewport INTERNAL:[^\n]*?logicalFrame=\[(\d+),\s*(\d+),\s*(\d+),\s*(\d+)\]", text)
        if match:
            x, y, right, bottom = map(int, match.groups())
            return right - x, bottom - y
        return None

    def interact(self, owner, body):
        with self._lock:
            if body.get("generation", self.generation) != self.generation:
                raise ValueError("运行设备已切换，请在新画面上重试")
            serial = self.serial(owner)
            event = body.get("event")
            if event in {"tap", "swipe"}:
                if not self._frame or self._frame[0] != (self.generation, owner, serial):
                    raise ValueError("请先等待当前设备画面")
                width, height = self._frame[2]["width"], self._frame[2]["height"]
                if self._video:
                    geometry = self._usb_geometry(serial)
                    if geometry and geometry != (width, height):
                        self._video.stop(); self._video = None; self._frame = None
                        raise ValueError("手机方向已改变，请等待新画面后重试")
                keys = ("x", "y", "x2", "y2") if event == "swipe" else ("x", "y")
                values = [body.get(key) for key in keys]
                if any(type(value) is not int or not 0 <= value < (width if index % 2 == 0 else height)
                       for index, value in enumerate(values)):
                    raise ValueError("点击或滑动坐标无效")
                parts = ["input", event, *map(str, values)]
                if event == "swipe":
                    parts.append(str(max(80, min(1000, int(body.get("duration", 250))))))
            elif event == "back":
                parts = ["input", "keyevent", "4"]
            elif event == "text" and re.fullmatch(r"[A-Za-z0-9_ .-]{1,80}", body.get("value", "")):
                parts = ["input", "text", body["value"].replace(" ", "%s")]
            else:
                raise ValueError("设备输入无效")
            bench.shell(serial, *parts, timeout=8)
            self._frame = None
            return {"ok": True, "generation": self.generation}

    def evidence(self, owner, package):
        try:
            return runtime_evidence(self.serial(owner), package)
        except (ValueError, RuntimeError, OSError, TimeoutError, subprocess.SubprocessError) as exc:
            return {"available": False, "reason": str(exc)[:300]}


class UsbDevice:
    """Manual phone tests preserve data and never uninstall or clear packages."""
    def __init__(self, serial):
        self.serial = serial
        self.owner = self.api = None

    def ensure(self, owner, api, progress, native_abis=()):
        if not any(d["id"] == self.serial and d["available"] for d in usb_devices()):
            raise ValueError("USB 手机已断开或未授权")
        actual = int(bench.shell(self.serial, "getprop", "ro.build.version.sdk"))
        if actual != api:
            raise ValueError(f"手机实际 API {actual} 与所选目标 API {api} 不符，请调整目标版本")
        self.owner, self.api = owner, actual
        progress("已连接 USB 真机；安装保留数据，不自动卸载应用")
        return self.serial

    def check(self, owner):
        if self.owner != owner:
            raise ValueError("当前任务尚未连接真机")

    def install_run(self, apk, facts):
        self.check(self.owner)
        package, launcher = facts["package"], facts["launcher"]
        if not re.fullmatch(r"[A-Za-z0-9_.]+", package) or not launcher or not re.fullmatch(r"[A-Za-z0-9_.$]+", launcher):
            raise ValueError("无效的应用包名或启动组件")
        if f"package:{package}" in bench.shell(self.serial, "pm", "list", "packages", "-s", package).splitlines():
            raise ValueError("不覆盖手机系统应用")
        with bench.tool_input(Path(apk)) as source:
            installed = bench.invoke([bench.ADB, "-s", self.serial, "install", "-r", source], 120)
        result = {"api": self.api, "abis": bench.shell(self.serial, "getprop", "ro.product.cpu.abilist").split(","),
                  "environment": "usb-phone", "install": {"exit": installed.returncode, "output": bench.output(installed)},
                  "data_policy": "preserve-no-uninstall-no-clear"}
        if installed.returncode == 0:
            launched = bench.invoke([bench.ADB, "-s", self.serial, "shell", "am", "start", "-W", "-n", package + "/" + launcher], 20)
            windows = bench.shell(self.serial, "dumpsys", "window", "displays", timeout=8)
            focus = re.search(r"mCurrentFocus=Window\{[^\r\n}]*?\bu\d+\s+([\w.]+)/", windows)
            result["launch"] = {"exit": launched.returncode, "output": bench.output(launched),
                                "focused_package": focus.group(1) if focus else None}
        return result
