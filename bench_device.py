"""Exclusive disposable AVD controller for interactive previews."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import tempfile
from offline_kit import discover_sdk
from storage_paths import configure_environment

ROOT = Path(__file__).resolve().parent
PATHS = configure_environment()
# Resource discovery deliberately avoids shutil.which/Android Studio and works
# when a portable EXE is launched with an empty PATH.
SDK = discover_sdk() or Path(os.environ.get("REVIVER_SDK", r"E:\Software\Android-Tool\Sdk"))
_legacy_arm_lab = Path(r"E:\Agent\android-reviver-lab")
_storage_explicit = bool(os.environ.get("LAZARUS_STORAGE_ROOT") or os.environ.get("LAZARUS_PORTABLE") or os.environ.get("LAZARUS_PORTABLE_ROOT"))
# Preserve the already paired 77 GiB ARM lab in normal upgrades.  Explicit
# portable/QA storage is intentionally isolated and never borrows that state.
LAB = Path(os.environ["REVIVER_LAB"]) if os.environ.get("REVIVER_LAB") else (
    _legacy_arm_lab if _legacy_arm_lab.is_dir() and not _storage_explicit else PATHS.runtime("android-lab")
)
ADB = SDK/"platform-tools/adb.exe"


def _build_tools() -> Path:
    explicit = os.environ.get("REVIVER_BUILD_TOOLS", "").strip()
    if explicit:
        return Path(explicit)
    preferred = SDK / "build-tools" / "36.0.0"
    if (preferred / "zipalign.exe").is_file() and (preferred / "lib" / "apksigner.jar").is_file():
        return preferred
    available = sorted((SDK / "build-tools").glob("*") if (SDK / "build-tools").is_dir() else (), reverse=True)
    return next((item for item in available if (item / "zipalign.exe").is_file() and (item / "lib" / "apksigner.jar").is_file()), preferred)


TOOLS = _build_tools()


def _powershell() -> str:
    """Locate PowerShell even when the portable verification run has no PATH."""
    found = shutil.which("pwsh") or shutil.which("powershell")
    if found:
        return found
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    windows_ps = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(windows_ps) if windows_ps.is_file() else "powershell"
PORT = {30:5570,34:5574,35:5576,36:5578}
ARM_LAB = "127.0.0.1:19636"
ARM_PAIRING = LAB/"redroid16/workbench-instance"


def paired_arm_lab():
    try:
        marker = ARM_PAIRING.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return False
    if not re.fullmatch(r"[a-f0-9]{64}", marker):
        return False
    try:
        result = invoke([ADB, "-s", ARM_LAB, "shell",
                         "cat /data/local/tmp/.reviver-instance && getprop ro.build.version.sdk && "
                         "getprop ro.product.device && getprop ro.product.cpu.abilist32 && getprop sys.boot_completed"], 12)
        values = result.stdout.decode().splitlines()
        return (result.returncode == 0 and len(values) == 5 and values[:3] == [marker, "36", "redroid_arm64"]
                and "armeabi-v7a" in values[3].split(",") and values[4] == "1")
    except (OSError, subprocess.TimeoutExpired):
        return False


def ensure_arm_lab(progress):
    if not ARM_PAIRING.is_file():
        return False
    if paired_arm_lab():
        return True
    progress("正在恢复已配对的离线 ARM 实验室，首次启动可能需要数分钟")
    with (ARM_PAIRING.parent/"workbench-relay.log").open("ab") as log:
        entry="--serve-arm-lab" if getattr(sys,"frozen",False) else ROOT/"tools/serve_arm_lab.py"
        relay=subprocess.Popen([sys.executable, entry],stdin=subprocess.DEVNULL,
                               stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
    deadline=time.monotonic()+1800
    # Another relay can already own the reserved port while Android finishes booting.
    while time.monotonic()<deadline:
        if paired_arm_lab():
            return True
        if relay.poll() is not None:
            try:
                attempt=invoke([ADB,"connect",ARM_LAB],10)
                if attempt.returncode or "connected to" not in output(attempt):
                    break
                observed=invoke([ADB,"-s",ARM_LAB,"shell","getprop","sys.boot_completed"],10)
                if observed.stdout.strip()==b"1":
                    return paired_arm_lab()
            except subprocess.TimeoutExpired:
                pass
        time.sleep(6)
    progress("配对的 ARM 环境未就绪，改用已配置的 SDK 镜像验证")
    return False


def invoke(args,timeout=120):
    return subprocess.run(list(map(str,args)),capture_output=True,timeout=timeout,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))


def output(proc):
    return (proc.stdout+proc.stderr).decode("utf-8","replace").strip()


def shell(serial,*parts,timeout=30):
    proc=invoke([ADB,"-s",serial,"shell",*parts],timeout)
    if proc.returncode:
        raise RuntimeError(output(proc))
    return output(proc)


def name_at(serial):
    proc=invoke([ADB,"-s",serial,"emu","avd","name"],6)
    return output(proc).splitlines()[0] if not proc.returncode and proc.stdout.strip() else None


def avd_ready(api):
    base=SDK/f"system-images/android-{api}"
    images=[base/tag/"x86_64/system.img" for tag in ("google_apis_playstore","google_apis","default")]
    cache=PATHS.runtime("android-lab")/"lab-sdk"
    images.append(cache/f"system-images/android-{api}/default/x86_64/system.img")
    legacy=Path(os.environ.get("LOCALAPPDATA","."))/"LegacyApkReviver/lab-sdk"
    images.append(legacy/f"system-images/android-{api}/default/x86_64/system.img")
    if api==30:
        old=Path(os.environ.get("REVIVER_OLD_SDK",r"D:\android\AndroidSDK"))
        images.append(old/"system-images/android-30/google_apis_playstore/x86/system.img")
    return any(p.is_file() for p in images)


def ready(api):
    return avd_ready(api) or (api == 36 and ARM_PAIRING.is_file() and
                              (ARM_PAIRING.parent/"guest-arm64-overlay.qcow2").is_file())


class Device:
    def __init__(self):
        self.owner=None
        self.serial=None
        self.api=None

    def check(self,owner):
        if self.owner != owner or not self.serial:
            raise ValueError("此任务尚未运行，或模拟器已关闭")
        matches = paired_arm_lab() if self.serial == ARM_LAB else name_at(self.serial)==f"ReviverBench{self.api}"
        if not matches:
            raise ValueError("此任务尚未运行，或模拟器已关闭")

    def stop(self):
        if self.serial and self.serial != ARM_LAB and name_at(self.serial)==f"ReviverBench{self.api}":
            invoke([ADB,"-s",self.serial,"emu","kill"],10)
            for _ in range(30):
                if self.serial not in output(invoke([ADB,"devices"],8)):
                    break
                time.sleep(.5)
        self.owner=self.serial=self.api=None

    def ensure(self,owner,api,progress,native_abis=()):
        needs_arm = bool({"armeabi", "armeabi-v7a", "arm64-v8a"}.intersection(native_abis)) and "x86_64" not in native_abis
        if api == 36 and (needs_arm or not avd_ready(api)) and ensure_arm_lab(progress):
            self.stop()
            self.serial,self.api,self.owner=ARM_LAB,api,owner
            progress("已选择配对的 Android 16 ARM 隔离环境，系统支持 32 位 ARM 原生库")
            return self.serial
        if self.owner==owner and self.api==api and name_at(self.serial)==f"ReviverBench{api}":
            return self.serial
        self.stop()
        if not avd_ready(api):
            raise ValueError(f"缺少 API {api} 模拟器镜像")
        serial=f"emulator-{PORT[api]}"
        name=f"ReviverBench{api}"
        if serial in output(invoke([ADB,"devices"],10)):
            if name_at(serial)!=name:
                raise RuntimeError("工作台端口已被其他设备占用")
            self.serial,self.api=serial,api
            self.stop()
        progress(f"正在冷启动 Android {api-20 if api>=34 else 11} 专用模拟器")
        # The detached emulator can inherit Windows pipe handles. Waiting for pipe EOF
        # would block until the emulator exits even though PowerShell already returned.
        with tempfile.TemporaryFile() as log:
            proc=subprocess.run(list(map(str,[_powershell(),"-NoProfile","-File",ROOT/"tools/start_lab.ps1",
                         "-Api",api,"-Name",name,"-Port",PORT[api],"-Sdk",SDK,"-Lab",LAB,"-ResetBench"])),
                         stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,timeout=40,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            if proc.returncode:
                log.seek(0)
                raise RuntimeError(log.read().decode("utf-8","replace")[-1200:])
        self.api,self.serial=api,serial
        for _ in range(100):
            boot=invoke([ADB,"-s",serial,"shell","getprop","sys.boot_completed"],10)
            if boot.stdout.strip()==b"1" and name_at(serial)==name:
                if shell(serial,"getprop","ro.build.version.sdk")!=str(api):
                    raise RuntimeError("目标系统版本不符")
                self.owner=owner
                shell(serial,"wm","dismiss-keyguard")
                return serial
            time.sleep(2)
        raise RuntimeError("模拟器启动超时，请检查专用 AVD 日志")

    def install_run(self,apk,facts):
        pkg=facts["package"]
        launcher=facts["launcher"]
        if not re.fullmatch(r"[A-Za-z0-9_.]+",pkg) or (launcher and not re.fullmatch(r"[A-Za-z0-9_.$]+",launcher)):
            raise ValueError("无效的包名或启动组件")
        # Only the job's private, pre-checked AVD may be altered.
        self.check(self.owner)
        known=shell(self.serial,"pm","list","packages",pkg).splitlines()
        if f"package:{pkg}" in known:
            if f"package:{pkg}" not in shell(self.serial,"pm","list","packages","-3",pkg).splitlines():
                raise ValueError("目标包名与系统应用重合")
            removed=invoke([ADB,"-s",self.serial,"uninstall",pkg])
            if removed.returncode or "Success" not in output(removed):
                raise RuntimeError("测试实例中旧安装未能清理")
        proc=invoke([ADB,"-s",self.serial,"install",apk],600 if self.serial==ARM_LAB else 120)
        result={"api":self.api,"abis":shell(self.serial,"getprop","ro.product.cpu.abilist").split(","),
                "install":{"exit":proc.returncode,"output":output(proc).replace(str(apk),apk.name)}}
        if not proc.returncode and launcher:
            proc=invoke([ADB,"-s",self.serial,"shell","am","start","-W","-n",pkg+"/"+launcher])
            result["launch"]={"exit":proc.returncode,"output":output(proc)}
            if proc.returncode==0:
                # am start may succeed while the permission controller or launcher owns the window.
                time.sleep(1)
                windows=invoke([ADB,"-s",self.serial,"shell","dumpsys","window","displays"],15)
                focus=re.search(r"(?m)^\s*mCurrentFocus=Window\{[^\r\n}]*?\bu\d+\s+([\w.]+)/",output(windows))
                result["launch"]["focused_package"]=focus.group(1) if focus else None
        return result
