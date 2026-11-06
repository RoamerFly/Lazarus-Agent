"""Prepare only the Android pieces required for one local verification target.

Nothing here edits an Android Studio SDK, a legacy lab, or an active AVD.  A
component is either reused read-only through a junction, or downloaded from its
publisher catalogue, SHA checked, safely extracted into staging and atomically
published below Lazarus' controlled runtime directory.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from zipfile import ZipFile
from typing import Callable


BASE = "https://dl.google.com/android/repository/"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from storage_paths import APP_NAME, configure_environment  # noqa: E402
from offline_kit import discover_java_home, discover_sdk  # noqa: E402


def managed_runtime_root() -> Path:
    """Return the new controlled runtime without writing legacy user data."""
    paths = configure_environment()
    if not paths.legacy_layout:
        return paths.runtime("android-lab")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / APP_NAME / "runtimes" / "android-lab"
    return paths.runtime("android-lab")


# Compatibility alias for callers of the older one-component downloader. It
# intentionally points at the new managed runtime, never LegacyApkReviver.
CACHE = managed_runtime_root()
MAX_ARCHIVE_MEMBERS = 100_000
MAX_UNCOMPRESSED_BYTES = 12 * 1024 * 1024 * 1024
BUILD_TOOLS_VERSION = "36.0.0"
Progress = Callable[[dict[str, object]], None]
HTTP_HEADERS = {"User-Agent": "Lazarus-Agent/0.1 (+local Android compatibility verification)"}


def _verify_archive(path: Path, expected_size: int, expected_digest: str, algorithm: str = "sha1") -> None:
    with path.open("rb") as raw:
        actual_digest = hashlib.file_digest(raw, algorithm).hexdigest()
    if path.stat().st_size != expected_size or actual_digest != expected_digest:
        raise ValueError(f"Archive does not match official catalog: {path}")


def _safe_extract(archive: Path, destination: Path) -> None:
    """Extract a ZIP only after rejecting traversal, links, and zip bombs."""
    destination = destination.resolve(strict=False)
    with ZipFile(archive) as bundle:
        entries = bundle.infolist()
        if len(entries) > MAX_ARCHIVE_MEMBERS:
            raise ValueError("Archive contains too many entries")
        if sum(entry.file_size for entry in entries) > MAX_UNCOMPRESSED_BYTES:
            raise ValueError("Archive uncompressed size exceeds lab safety limit")
        for entry in entries:
            # A symlink can make a lexically safe later entry escape destination.
            unix_type = (entry.external_attr >> 16) & 0o170000
            if unix_type == 0o120000:
                raise ValueError(f"Refusing symbolic link in archive: {entry.filename}")
            target = (destination / entry.filename).resolve(strict=False)
            if target != destination and destination not in target.parents:
                raise ValueError(f"Refusing path outside destination: {entry.filename}")
        bundle.extractall(destination, entries)


def _extract_without_overwrite(archive: Path, destination: Path) -> None:
    """Stage on the destination volume and publish only a complete extraction."""
    if destination.exists():
        raise FileExistsError(
            f"Refusing to overwrite existing component: {destination}. "
            "Remove it manually only after confirming it is not in use."
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    # NamedTemporaryFile/TemporaryDirectory under destination.parent guarantees
    # that the final rename stays on one volume and is atomic on Windows.
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix=".extract-") as temporary:
        staged = Path(temporary) / "payload"
        staged.mkdir()
        _safe_extract(archive, staged)
        if not any(staged.iterdir()):
            raise ValueError("Archive extraction produced no files")
        # Do not use shutil.move: it may degrade to copy+delete across volumes.
        staged.replace(destination)


def _extract_member_without_overwrite(archive: Path, destination: Path, member: str) -> None:
    """Publish a specific directory from a verified archive without overwrite."""
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite existing component: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix=".extract-") as temporary:
        staged = Path(temporary) / "payload"
        staged.mkdir()
        _safe_extract(archive, staged)
        source = staged / member
        if not source.is_dir():
            raise ValueError(f"Official archive lacks expected component root: {member}")
        source.replace(destination)


def _extract_matching_without_overwrite(archive: Path, destination: Path, matcher: Callable[[Path], bool]) -> None:
    """Safely extract and atomically publish the unique directory matching a tool signature."""
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite existing component: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix=".extract-") as temporary:
        staged = Path(temporary) / "payload"
        staged.mkdir()
        _safe_extract(archive, staged)
        matches = [item for item in staged.rglob("*") if item.is_dir() and matcher(item)]
        if len(matches) != 1:
            raise ValueError(f"Official archive has {len(matches)} matching component roots; expected exactly one")
        matches[0].replace(destination)


def _event(progress: Progress | None, phase: str, component: str, message: str, api: int | None = None, **extra: object) -> None:
    if progress:
        progress({"phase": phase, "component": component, "message": message, "api": api, **extra})


def _google_spec(component: str, api: int | None = None) -> dict[str, object]:
    image = component == "system-image"
    prefix = "sys-img/android/" if image else ""
    index = prefix + "sys-img2-1.xml" if image else "repository2-1.xml"
    package_id = f"system-images;android-{api};default;x86_64" if image else component
    with urllib.request.urlopen(urllib.request.Request(BASE + index, headers=HTTP_HEADERS), timeout=30) as response:
        catalog = ET.parse(response)
    packages = [entry for entry in catalog.getroot().iter()
                if entry.tag.endswith("remotePackage") and entry.get("path") == package_id]
    pkg = next((entry for entry in packages if entry.find("{*}channelRef") is None or entry.find("{*}channelRef").get("ref") == "channel-0"), None)
    if pkg is None:
        raise ValueError(f"Official catalog has no stable Windows package: {package_id}")
    archives = [entry for entry in pkg.iter() if entry.tag.endswith("archive")]
    archive = next((entry for entry in archives if (entry.findtext(".//{*}host-os") or "windows") == "windows"), None)
    if archive is None:
        raise ValueError(f"Official catalog has no Windows archive: {package_id}")
    url = archive.findtext(".//{*}complete/{*}url")
    digest = archive.findtext(".//{*}complete/{*}checksum")
    size = archive.findtext(".//{*}complete/{*}size")
    if not url or not digest or not size:
        raise ValueError(f"Official catalog has incomplete checksum data: {package_id}")
    return {"name": url, "url": BASE + prefix + url,
            "fallback_url": BASE.replace("dl.google.com", "redirector.gvt1.com/edgedl") + prefix + url,
            "size": int(size), "digest": digest, "algorithm": "sha1"}


def _temurin_spec() -> dict[str, object]:
    """Get a publisher-provided SHA-256 for the current Temurin 21 Windows JRE."""
    url = "https://api.adoptium.net/v3/assets/latest/21/hotspot?os=windows&architecture=x64&image_type=jre&jvm_impl=hotspot&vendor=eclipse"
    with urllib.request.urlopen(urllib.request.Request(url, headers=HTTP_HEADERS), timeout=30) as response:
        assets = json.load(response)
    if not isinstance(assets, list) or not assets:
        raise ValueError("Temurin catalog returned no Windows x64 JRE")
    package = assets[0].get("binary", {}).get("package", {})
    link, digest, size = package.get("link"), package.get("checksum"), package.get("size")
    if not isinstance(link, str) or not isinstance(digest, str) or not isinstance(size, int):
        raise ValueError("Temurin catalog omitted package hash or size")
    return {"name": Path(link).name, "url": link, "size": size, "digest": digest, "algorithm": "sha256"}


def _is_cancelled(cancel: object | None) -> bool:
    if cancel is None:
        return False
    method = getattr(cancel, "is_set", None)
    return bool(method()) if callable(method) else bool(cancel()) if callable(cancel) else bool(cancel)


def _check_continue(cancel: object | None, deadline: float | None) -> None:
    if _is_cancelled(cancel):
        raise InterruptedError("按需准备已取消；已下载部分保留，稍后可续传")
    if deadline is not None and time.monotonic() > deadline:
        raise TimeoutError("按需准备超时；已下载部分保留，稍后可续传")


def _download(spec: dict[str, object], downloads: Path, progress: Progress | None, component: str, api: int | None,
              cancel: object | None = None, deadline: float | None = None) -> Path:
    name, size, digest, algorithm = (str(spec[key]) for key in ("name", "size", "digest", "algorithm"))
    expected_size = int(size)
    out = downloads / name
    downloads.mkdir(parents=True, exist_ok=True)
    if out.is_file():
        _verify_archive(out, expected_size, digest, algorithm)
        _event(progress, "verify", component, "已复用已校验下载包", api, current=expected_size, total=expected_size)
        return out
    temporary = out.with_name(out.name + ".part")
    current = temporary.stat().st_size if temporary.is_file() else 0
    if current > expected_size:
        temporary.unlink()
        current = 0
    _event(progress, "download", component, "正在从官方源下载" if not current else "正在续传官方源下载", api, current=current, total=expected_size)
    _check_continue(cancel, deadline)
    headers = dict(HTTP_HEADERS)
    if current:
        headers["Range"] = f"bytes={current}-"
    request = urllib.request.Request(str(spec["url"]), headers=headers)
    try:
        response = urllib.request.urlopen(request, timeout=90)
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        fallback = spec.get("fallback_url")
        if not fallback:
            raise
        _check_continue(cancel, deadline)
        _event(progress, "download", component, "主下载地址不可用，正在尝试官方备用地址",
               api, current=current, total=expected_size)
        response = urllib.request.urlopen(urllib.request.Request(str(fallback), headers=headers), timeout=90)
    with response as source:
        # Some mirrors ignore Range. Avoid appending a full archive to a partial.
        append = current > 0 and getattr(source, "status", None) == 206
        if current and not append:
            current = 0
        with temporary.open("ab" if append else "wb") as destination:
            while chunk := source.read(1024 * 1024):
                _check_continue(cancel, deadline)
                destination.write(chunk)
                current += len(chunk)
                _event(progress, "download", component, "正在下载", api, current=current, total=expected_size)
    _event(progress, "verify", component, "正在校验发布方哈希", api, current=current, total=expected_size)
    _verify_archive(temporary, expected_size, digest, algorithm)
    temporary.replace(out)
    return out


def fetch_component(component: str) -> Path:
    image = component != "emulator"
    prefix = "sys-img/android/" if image else ""
    index = prefix + "sys-img2-1.xml" if image else "repository2-1.xml"
    with urllib.request.urlopen(BASE + index, timeout=30) as response:
        catalog = ET.parse(response)
    package_id = f"system-images;android-{component};default;x86_64" if image else "emulator"
    pkg = next(p for p in catalog.getroot() if p.get("path") == package_id and
               (p.find("channelRef") is None or p.find("channelRef").get("ref") == "channel-0"))
    archive = next(a for a in pkg.findall(".//archive") if a.findtext("host-os") in (None, "windows"))
    name = archive.findtext("complete/url")
    digest = archive.findtext("complete/checksum")
    size = int(archive.findtext("complete/size"))
    out = CACHE / "downloads" / name
    out.parent.mkdir(parents=True, exist_ok=True)
    if not out.exists():
        with tempfile.NamedTemporaryFile(dir=out.parent, suffix=".part", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            with urllib.request.urlopen(BASE.replace("dl.google.com", "redirector.gvt1.com/edgedl") + prefix + name, timeout=90) as src, tmp_path.open("wb") as dst:
                shutil.copyfileobj(src, dst, length=1024*1024)
            _verify_archive(tmp_path, size, digest)
            tmp_path.replace(out)
        finally:
            tmp_path.unlink(missing_ok=True)
    _verify_archive(out, size, digest)
    dest = CACHE / "lab-sdk"
    if image:
        dest = dest / "system-images" / f"android-{component}" / "default"
    _extract_without_overwrite(out, dest)
    print(f"Verified {name} ({digest}); extracted to {dest}", flush=True)
    return dest


def _tool_root_complete(root: Path) -> bool:
    tools = root / "build-tools" / BUILD_TOOLS_VERSION
    return ((root / "platform-tools" / "adb.exe").is_file() and
            (root / "emulator" / "emulator.exe").is_file() and
            (tools / "zipalign.exe").is_file() and (tools / "lib" / "apksigner.jar").is_file())


def _image_complete(root: Path, api: int) -> bool:
    return (root / "system-images" / f"android-{api}" / "default" / "x86_64" / "system.img").is_file()


def _java_complete(home: Path) -> bool:
    return (home / "bin" / "java.exe").is_file() and (home / "bin" / "keytool.exe").is_file()


def inventory(api: int, runtime_root: Path | None = None) -> dict[str, object]:
    """Fast local-only readiness check for UI; it performs no network request."""
    if api not in (34, 35, 36):
        raise ValueError("Supported on-demand verification APIs are 34, 35 and 36")
    runtime = (runtime_root or managed_runtime_root()).resolve(strict=False)
    sdk, java = runtime / "lab-sdk", runtime / "java"
    components = {
        "platform-tools": sdk / "platform-tools",
        "emulator": sdk / "emulator",
        "build-tools": sdk / "build-tools" / BUILD_TOOLS_VERSION,
        "system-image": sdk / "system-images" / f"android-{api}" / "default" / "x86_64",
        "java": java,
    }
    ready = {
        "platform-tools": (components["platform-tools"] / "adb.exe").is_file(),
        "emulator": (components["emulator"] / "emulator.exe").is_file(),
        "build-tools": (components["build-tools"] / "zipalign.exe").is_file() and (components["build-tools"] / "lib" / "apksigner.jar").is_file(),
        "system-image": (components["system-image"] / "system.img").is_file(),
        "java": _java_complete(java),
    }
    sizes = {name: sum(item.stat().st_size for item in path.rglob("*") if item.is_file()) if path.is_dir() else 0
             for name, path in components.items()}
    return {"api": api, "runtime": str(runtime), "ready": all(ready.values()), "components": ready,
            "missing": [name for name, available in ready.items() if not available], "payloadSize": sum(sizes.values()),
            "componentSizes": sizes, "networkRequired": not all(ready.values())}


def _source_sdks(destination: Path) -> list[Path]:
    """Read-only candidates that can satisfy a component without a download."""
    candidates: list[Path] = []
    selected = discover_sdk()
    if selected:
        candidates.append(selected)
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        candidates.append(Path(local) / "LegacyApkReviver" / "lab-sdk")
    candidates.append(Path(r"E:\Software\Android-Tool\Sdk"))
    seen: set[Path] = set()
    result: list[Path] = []
    for root in candidates:
        root = root.resolve(strict=False)
        if root != destination.resolve(strict=False) and root not in seen and root.is_dir():
            result.append(root)
            seen.add(root)
    return result


def _link_existing(source: Path, destination: Path) -> bool:
    """Create a read-only directory junction/symlink; never copy or alter source."""
    if destination.exists():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(source, destination, target_is_directory=True)
        return True
    except OSError:
        # Directory junctions do not require the Developer Mode privilege that
        # symlinks require on common Windows installations.
        if os.name == "nt":
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(destination), str(source)],
                                    capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return result.returncode == 0 and destination.is_dir()
        return False


def _install_google_component(component: str, api: int | None, sdk: Path, downloads: Path, progress: Progress | None,
                              cancel: object | None = None, deadline: float | None = None) -> Path:
    spec = _google_spec(component, api)
    archive = _download(spec, downloads, progress, component, api, cancel, deadline)
    if component == "platform-tools":
        destination, matcher = sdk / "platform-tools", lambda path: (path / "adb.exe").is_file()
    elif component == "emulator":
        destination, matcher = sdk / "emulator", lambda path: (path / "emulator.exe").is_file()
    elif component == f"build-tools;{BUILD_TOOLS_VERSION}":
        destination, matcher = sdk / "build-tools" / BUILD_TOOLS_VERSION, lambda path: (path / "zipalign.exe").is_file() and (path / "lib" / "apksigner.jar").is_file()
    elif component == "system-image" and api is not None:
        destination, matcher = sdk / "system-images" / f"android-{api}" / "default" / "x86_64", lambda path: path.name == "x86_64" and (path / "system.img").is_file()
    else:
        raise ValueError(f"Unsupported component: {component}")
    _event(progress, "extract", component, "正在安全解压并原子发布", api)
    _extract_matching_without_overwrite(archive, destination, matcher)
    archive.unlink(missing_ok=True)
    return destination


def _install_temurin(destination: Path, downloads: Path, progress: Progress | None,
                     cancel: object | None = None, deadline: float | None = None) -> Path:
    spec = _temurin_spec()
    archive = _download(spec, downloads, progress, "java", None, cancel, deadline)
    if destination.exists():
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix=".extract-") as temporary:
        staged = Path(temporary) / "payload"
        staged.mkdir()
        _safe_extract(archive, staged)
        homes = [item.parent.parent for item in staged.rglob("java.exe") if item.parent.name == "bin" and (item.parent / "keytool.exe").is_file()]
        if len(homes) != 1:
            raise ValueError("Temurin archive did not contain exactly one Java runtime")
        homes[0].replace(destination)
    archive.unlink(missing_ok=True)
    return destination


def prepare_runtime(api: int, progress: Progress | None = None, runtime_root: Path | None = None,
                    cancel: object | None = None, timeout_seconds: float = 3600,
                    terms_accepted: bool = False) -> dict[str, object]:
    """Asynchronously callable, idempotent preparation for exactly one API.

    The caller must run this only after an explicit user action. It does not
    start an emulator; it merely makes `lab-sdk` and a Java runtime available.
    """
    if api not in (34, 35, 36):
        raise ValueError("Supported on-demand verification APIs are 34, 35 and 36")
    runtime = (runtime_root or managed_runtime_root()).resolve(strict=False)
    deadline = time.monotonic() + timeout_seconds if timeout_seconds > 0 else None
    sdk, downloads, java = runtime / "lab-sdk", runtime / "downloads", runtime / "java"
    sdk.mkdir(parents=True, exist_ok=True)
    _event(progress, "inspect", "runtime", "正在检查本机可复用组件", api)
    reused: list[str] = []
    installed: list[str] = []
    sources = _source_sdks(sdk)

    expected = (
        ("platform-tools", sdk / "platform-tools", lambda root: root / "platform-tools"),
        ("emulator", sdk / "emulator", lambda root: root / "emulator"),
        ("build-tools", sdk / "build-tools" / BUILD_TOOLS_VERSION, lambda root: root / "build-tools" / BUILD_TOOLS_VERSION),
        ("system-image", sdk / "system-images" / f"android-{api}" / "default" / "x86_64", lambda root: root / "system-images" / f"android-{api}" / "default" / "x86_64"),
    )
    for label, destination, from_source in expected:
        complete = (destination / ("adb.exe" if label == "platform-tools" else "emulator.exe" if label == "emulator" else "system.img" if label == "system-image" else "zipalign.exe")).is_file()
        if complete:
            reused.append(label)
            _event(progress, "ready", label, "已存在，跳过下载", api)
            continue
        source = next((from_source(root) for root in sources if from_source(root).is_dir() and
                       (from_source(root) / ("adb.exe" if label == "platform-tools" else "emulator.exe" if label == "emulator" else "system.img" if label == "system-image" else "zipalign.exe")).is_file()), None)
        if source and _link_existing(source, destination):
            reused.append(label)
            _event(progress, "ready", label, "已复用本机组件（只读链接）", api)
            continue
        component = {"platform-tools": "platform-tools", "emulator": "emulator", "build-tools": f"build-tools;{BUILD_TOOLS_VERSION}", "system-image": "system-image"}[label]
        if terms_accepted is not True:
            raise PermissionError("必须由本机用户先确认 Android SDK 条款，才允许下载缺失组件")
        _check_continue(cancel, deadline)
        _install_google_component(component, api if label == "system-image" else None, sdk, downloads, progress, cancel, deadline)
        installed.append(label)
        _event(progress, "ready", label, "已准备完成", api)

    if _java_complete(java):
        reused.append("java")
        _event(progress, "ready", "java", "已存在，跳过下载", api)
    else:
        existing_java = discover_java_home()
        if existing_java and _link_existing(existing_java, java):
            reused.append("java")
            _event(progress, "ready", "java", "已复用本机 Java（只读链接）", api)
        else:
            if terms_accepted is not True:
                raise PermissionError("必须由本机用户先确认 Temurin 下载条款，才允许下载缺失 Java")
            _install_temurin(java, downloads, progress, cancel, deadline)
            installed.append("java")
            _event(progress, "ready", "java", "已准备完成", api)
    if not (_tool_root_complete(sdk) and _image_complete(sdk, api) and _java_complete(java)):
        raise RuntimeError("按需准备结束后仍缺少验证运行时组件")
    os.environ["REVIVER_SDK"] = str(sdk)
    os.environ["REVIVER_JAVA_HOME"] = str(java)
    # bench_device imports SDK/ADB/TOOLS as module constants. Refresh an
    # already running workbench in-process when it has been imported.
    loaded = sys.modules.get("bench_device")
    if loaded and hasattr(loaded, "refresh_toolchain"):
        loaded.refresh_toolchain(sdk)
    _event(progress, "ready", "runtime", "目标验证环境已就绪，尚未启动模拟器", api)
    return {"ok": True, "api": api, "sdk": str(sdk), "java_home": str(java), "runtime": str(runtime),
            "reused": reused, "installed": installed,
            "required": {"platform_tools": True, "emulator": True, "build_tools": BUILD_TOOLS_VERSION,
                         "system_image": f"android-{api};default;x86_64", "java": True}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("component", choices=["34", "35", "36", "emulator", "prepare", "inventory"])
    parser.add_argument("--api", type=int, choices=(34, 35, 36))
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--timeout", type=float, default=3600)
    parser.add_argument("--accept-sdk-terms", action="store_true", help="确认已由本机用户接受发布方条款")
    args = parser.parse_args()
    if args.component in {"prepare", "inventory"}:
        if args.api is None:
            parser.error("--api is required for prepare/inventory")
        if args.component == "inventory":
            print(json.dumps(inventory(args.api, args.runtime_root), ensure_ascii=False, indent=2))
        else:
            def show(event: dict[str, object]) -> None:
                print(json.dumps(event, ensure_ascii=False), flush=True)
            if not args.accept_sdk_terms:
                parser.error("prepare requires --accept-sdk-terms after the user has accepted the publisher terms")
            print(json.dumps(prepare_runtime(args.api, show, args.runtime_root, timeout_seconds=args.timeout, terms_accepted=True), ensure_ascii=False, indent=2))
    else:
        fetch_component(args.component)
