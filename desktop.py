"""Windows desktop host for the loopback-only Lazarus Agent workbench."""
import ctypes
import json
import logging
import os
from pathlib import Path
import socket
import sys
import threading
import time
from urllib.error import URLError
from urllib.request import urlopen

from app_version import APP_NAME, APP_VERSION
from storage_paths import configure_environment

# Must run before importing workbench/reviver: those legacy modules read their
# storage environment at import time.
PATHS = configure_environment()


def claim_desktop():
    """Hold one native window per storage location, focusing it on repeat launch."""
    import hashlib
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    identity = hashlib.sha256(str(PATHS.root).casefold().encode()).hexdigest()[:20]
    handle = kernel.CreateMutexW(None, False, "Local\\Lazarus-Agent-" + identity)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() != 183:
        return handle
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle(handle)
    user = ctypes.windll.user32
    user.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user.FindWindowW.restype = wintypes.HWND
    user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user.SetForegroundWindow.argtypes = [wintypes.HWND]
    user.IsIconic.argtypes = [wintypes.HWND]
    for _ in range(100):
        window = user.FindWindowW(None, APP_NAME)
        if window:
            if user.IsIconic(window):
                user.ShowWindow(window, 9)
            user.SetForegroundWindow(window)
            return None
        time.sleep(.1)
    raise RuntimeError("工作台仍在启动，请稍后重新打开。")


def show_window(url):
    import webview

    from webview.platforms.winforms import is_chromium
    if not is_chromium:
        raise RuntimeError("请安装 Microsoft Edge WebView2 Runtime 后重新打开工作台。下载地址 https://go.microsoft.com/fwlink/p/?LinkId=2124703")

    # WebView2 has its own application profile and no browser sign-in UI.
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("RoamerFly.LazarusAgent")
    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.create_window(APP_NAME, url, width=1280, height=900, min_size=(960, 640), maximized=True)
    webview.start(gui="edgechromium", private_mode=False,
                  storage_path=str(PATHS.data / "desktop-webview2"),
                  icon=str(Path(__file__).resolve().parent / "web/assets/lazarus.ico"))


def existing_app(url):
    try:
        with urlopen(url + "/api/config", timeout=3) as result:
            return json.load(result).get("desktop_protocol") == 1
    except (OSError, ValueError, URLError):
        return False


def main():
    if "--version" in sys.argv:
        print(f"{APP_NAME} {APP_VERSION}")
        return
    if "--self-test" in sys.argv:
        payload = {"name": APP_NAME, "version": APP_VERSION, "platform": "Windows" if os.name == "nt" else "unsupported"}
        from modification_runner import capability
        from modification_tasks import CAPABILITIES
        for kind in CAPABILITIES:
            adapter, probe = capability(kind)
            if not all(callable(tool) for tool in (adapter.inspect, adapter.apply, probe)):
                raise RuntimeError(f"改造工具未完整打包：{kind}")
        if not (Path(__file__).resolve().parent / "web/modifications.js").is_file():
            raise RuntimeError("缺少改造工作台页面脚本")
        from UnityPy.helpers.Tpk import get_typetree
        from auto_translation import is_command
        if not is_command("汉化成中文") or not get_typetree().ClassInformation:
            raise RuntimeError("自动汉化资源分析器未完整打包")
        from ctranslate2 import Translator
        from sentencepiece import SentencePieceProcessor
        from opencc import OpenCC
        from rapidocr_onnxruntime import RapidOCR
        if not callable(Translator) or not callable(SentencePieceProcessor) or OpenCC("t2s").convert("龍") != "龙":
            raise RuntimeError("英译中推理组件不可用")
        RapidOCR(intra_op_num_threads=1, inter_op_num_threads=1)
        import webview
        if not callable(webview.create_window) or not (Path(__file__).resolve().parent / "web/assets/lazarus.ico").is_file():
            raise RuntimeError("桌面窗口或图标组件未完整打包")
        payload["desktop_renderer"] = "webview2"
        payload["chat_localization"] = ["android-resources", "unity4-ngui", "local-mt", "texture-ocr"]
        payload["modifications"] = sorted(CAPABILITIES)
        print(json.dumps(payload, ensure_ascii=False))
        return
    if "--apply-update" in sys.argv:
        from apply_update import main as apply_main
        apply_main()
        return
    if "--serve-arm-lab" in sys.argv:
        from tools.serve_arm_lab import serve
        serve()
        return
    root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    for parent in (root, *list(root.parents)[:3]):
        refs = parent / "evidence/pacific-rim"
        if (refs / "pic-trial/api30-screen.png").exists():
            os.environ.setdefault("REVIVER_GAME_REFERENCES", str(refs))
            break
    port = int(os.environ.get("REVIVER_PORT", "8767"))
    url = f"http://127.0.0.1:{port}"
    mutex = claim_desktop()
    if mutex is None:
        return
    server = None
    service = None
    sock = socket.socket()
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            if not existing_app(url):
                raise RuntimeError(f"本地端口 {port} 已被占用，请退出旧工作台后重试。")
        else:
            import uvicorn
            import workbench
            server = uvicorn.Server(uvicorn.Config(workbench.app, host="127.0.0.1", port=port,
                                    log_config=None, access_log=False, loop="asyncio", http="h11", ws="none"))
            service = threading.Thread(target=server.run, kwargs={"sockets":[sock]})
            service.start()
            for _ in range(100):
                if server.started:
                    break
                if not service.is_alive():
                    raise RuntimeError("本地服务启动失败，请查看 desktop.log。")
                time.sleep(.1)
            else:
                raise RuntimeError("本地服务启动超时。")
        show_window(url)
    finally:
        if service:
            # Finish an accepted repair even when the window is closed.
            with workbench.operation:
                server.should_exit=True
            service.join()
        sock.close()
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(mutex))


if __name__=="__main__":
    logging.basicConfig(filename=PATHS.logs/"desktop.log",level=logging.INFO,encoding="utf8")
    try:
        main()
    except Exception as error:
        logging.exception("Desktop startup failed")
        if "--serve-arm-lab" not in sys.argv:
            ctypes.windll.user32.MessageBoxW(None,str(error),"Lazarus Agent",0x10)
        sys.exit(1)
