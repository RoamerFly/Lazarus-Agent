"""Windows desktop host for the loopback-only Lazarus Agent workbench."""
import ctypes
import json
import logging
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from urllib.error import URLError
from urllib.request import urlopen

HOME = Path(os.environ["LOCALAPPDATA"]) / "LegacyApkReviver"
HOME.mkdir(parents=True, exist_ok=True)


def find_edge():
    for base in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        path = Path(os.environ.get(base, ".")) / "Microsoft/Edge/Application/msedge.exe"
        if path.is_file():
            return path
    raise RuntimeError("请先安装 Microsoft Edge，再启动桌面工作台。")


def existing_app(url):
    try:
        with urlopen(url + "/api/config", timeout=3) as result:
            return json.load(result).get("desktop_protocol") == 1
    except (OSError, ValueError, URLError):
        return False


def main():
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
    edge = find_edge()
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
        profile = HOME / "desktop-edge"
        subprocess.run([str(edge), f"--app={url}", f"--user-data-dir={profile}",
                        "--no-first-run", "--disable-first-run-ui", "--window-size=1280,900"],check=True)
    finally:
        if service:
            # Finish an accepted repair even when the window is closed.
            with workbench.operation:
                server.should_exit=True
            service.join()
        sock.close()


if __name__=="__main__":
    logging.basicConfig(filename=HOME/"desktop.log",level=logging.INFO,encoding="utf8")
    try:
        main()
    except Exception as error:
        logging.exception("Desktop startup failed")
        if "--serve-arm-lab" not in sys.argv:
            ctypes.windll.user32.MessageBoxW(None,str(error),"Lazarus Agent",0x10)
        sys.exit(1)
