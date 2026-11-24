"""User-selected APK import through the desktop's native file picker."""
import json
from pathlib import Path
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def apk_directory():
    root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    folder = root / "old_apk"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


class DesktopImport:
    def __init__(self, url):
        self._url = url
        self._window = None

    def choose_apk(self, api):
        """Only a path chosen in the native dialog may enter the import route."""
        from webview import FileDialog
        if type(api) is not int or not 24 <= api <= 36:
            return {"error": "请选择有效的 Android 版本"}
        try:
            files = self._window.create_file_dialog(
                FileDialog.OPEN, directory=str(apk_directory()), allow_multiple=False,
                file_types=("Android APK (*.apk)",))
            if not files:
                return {"cancelled": True}
            path = Path(files[0])
            if path.suffix.lower() != ".apk" or not path.is_file():
                raise ValueError("请选择 APK 文件")
            if path.stat().st_size > 320 * 1024**2:
                raise ValueError("仅接受 320 MiB 以内的单个 APK")
            with urlopen(self._url + "/api/config", timeout=10) as response:
                token = json.load(response)["token"]
            with path.open("rb") as source:
                request = Request(self._url + f"/api/import/{api}", data=source,
                                  headers={"x-reviver-token": token,
                                           "Content-Length": str(path.stat().st_size),
                                           "Content-Type": "application/octet-stream"}, method="POST")
                with urlopen(request, timeout=180) as response:
                    return {"job": json.load(response)}
        except HTTPError as exc:
            try:
                message = json.load(exc).get("detail", "APK 导入失败")
            except (ValueError, OSError):
                message = "APK 导入失败"
            return {"error": str(message)[:1200]}
        except (OSError, ValueError) as exc:
            return {"error": str(exc)[:1200]}
