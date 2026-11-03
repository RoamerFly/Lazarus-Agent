"""Build and discover the local-only Android verification resource kit.

The Android SDK, emulator system images and the Java runtime are intentionally
*not* versioned in this repository.  This module makes a deterministic local
kit from components already installed by its licence holder, and records a
SHA-256 manifest so the desktop application can use it with an otherwise empty
PATH.  It is suitable for a same-machine competition demonstration, not for
redistribution of Google SDK packages.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Iterable

from storage_paths import configure_environment


KIT_NAME = "Lazarus-Agent-Offline-Lab-Windows-x64"
KIT_SCHEMA = 1
APIS = (34, 35, 36)


def _program_root() -> Path:
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent


def _sdk_looks_usable(root: Path) -> bool:
    return (root / "platform-tools" / "adb.exe").is_file() and (root / "emulator" / "emulator.exe").is_file()


def default_kit_root() -> Path:
    """Return the preferred sibling kit, followed by the controlled runtime."""
    override = os.environ.get("REVIVER_OFFLINE_KIT", "").strip()
    if override:
        return Path(override).expanduser().resolve(strict=False)
    program = _program_root()
    sibling = program / KIT_NAME
    if sibling.is_dir():
        return sibling
    # Source-tree resources are convenient for a portable demo build.
    embedded = program / "resources" / KIT_NAME
    if embedded.is_dir():
        return embedded
    return configure_environment().runtime("android-lab") / KIT_NAME


def discover_sdk() -> Path | None:
    """Find an SDK without relying on PATH or Android Studio configuration."""
    candidates: list[Path] = []
    explicit = os.environ.get("REVIVER_SDK", "").strip()
    if explicit:
        candidates.append(Path(explicit))
    kit = default_kit_root()
    candidates.extend((kit / "android-sdk", kit / "lab-sdk"))
    # Existing controlled runtime is a source for the first kit build.
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        candidates.append(Path(local) / "Lazarus-Agent" / "runtimes" / "android-lab" / "lab-sdk")
    candidates.append(configure_environment().runtime("android-lab") / "lab-sdk")
    if local:
        candidates.append(Path(local) / "LegacyApkReviver" / "lab-sdk")
    candidates.append(Path(r"E:\Software\Android-Tool\Sdk"))
    seen: set[Path] = set()
    for candidate in candidates:
        candidate = candidate.expanduser().resolve(strict=False)
        if candidate not in seen and _sdk_looks_usable(candidate):
            return candidate
        seen.add(candidate)
    return None


def discover_java_home() -> Path | None:
    """Find a bundled Java first, then explicit JAVA_HOME; no PATH assumption."""
    candidates: list[Path] = []
    explicit = os.environ.get("REVIVER_JAVA_HOME", "").strip()
    if explicit:
        candidates.append(Path(explicit))
    candidates.append(default_kit_root() / "jre")
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        candidates.append(Path(local) / "Lazarus-Agent" / "runtimes" / "android-lab" / "java")
    candidates.append(configure_environment().runtime("android-lab") / "java")
    configured = os.environ.get("JAVA_HOME", "").strip()
    if configured:
        candidates.append(Path(configured))
    for home in candidates:
        if (home / "bin" / "java.exe").is_file() and (home / "bin" / "keytool.exe").is_file():
            return home.resolve(strict=False)
    return None


def java_environment(base: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    home = discover_java_home()
    if home:
        bin_dir = str(home / "bin")
        env["JAVA_HOME"] = str(home)
        env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
    return env


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_tree(source: Path, target: Path) -> list[dict[str, object]]:
    copied: list[dict[str, object]] = []
    for item in source.rglob("*"):
        relative = item.relative_to(source)
        destination = target / relative
        if item.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        elif item.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, destination)
            copied.append({"path": str(destination), "sha256": _sha256(destination), "bytes": destination.stat().st_size})
    return copied


def _require(source: Path, message: str) -> None:
    if not source.exists():
        raise FileNotFoundError(message + f"：{source}")


def build_kit(destination: Path, sdk: Path, java_home: Path, image_sdk: Path | None = None) -> dict[str, object]:
    """Copy a fully usable, licence-holder-local kit and write its manifest."""
    sdk, java_home = (path.resolve(strict=True) for path in (sdk, java_home))
    image_sdk = (image_sdk or sdk).resolve(strict=True)
    destination = destination.resolve(strict=False)
    if destination == sdk or destination == java_home or sdk in destination.parents or java_home in destination.parents:
        raise ValueError("离线资源包目标目录不能位于 SDK 或 Java 源目录内部")
    _require(sdk / "platform-tools" / "adb.exe", "SDK 缺少 adb")
    _require(sdk / "emulator" / "emulator.exe", "SDK 缺少 emulator")
    _require(java_home / "bin" / "java.exe", "Java 运行时缺少 java.exe")
    _require(java_home / "bin" / "keytool.exe", "Java 运行时缺少 keytool.exe")
    for api in APIS:
        _require(image_sdk / "system-images" / f"android-{api}" / "default" / "x86_64" / "system.img", f"缺少 API {api} AOSP x86_64 镜像")
    build_tools = sorted((sdk / "build-tools").glob("*"), reverse=True)
    tools = next((item for item in build_tools if (item / "zipalign.exe").is_file() and (item / "lib" / "apksigner.jar").is_file()), None)
    if tools is None:
        raise FileNotFoundError("SDK 缺少含 zipalign.exe 和 apksigner.jar 的 build-tools")
    if destination.exists():
        raise FileExistsError(f"目标已存在，为避免混合资源拒绝覆盖：{destination}")
    destination.mkdir(parents=True)
    sdk_destination = destination / "android-sdk"
    copied: list[dict[str, object]] = []
    for component in ("platform-tools", "emulator", "licenses"):
        source = sdk / component
        if source.is_dir():
            copied += _copy_tree(source, sdk_destination / component)
    copied += _copy_tree(tools, sdk_destination / "build-tools" / tools.name)
    for api in APIS:
        source = image_sdk / "system-images" / f"android-{api}" / "default" / "x86_64"
        copied += _copy_tree(source, sdk_destination / "system-images" / f"android-{api}" / "default" / "x86_64")
    copied += _copy_tree(java_home, destination / "jre")
    # Keep paths relative so the same kit remains verifiable after being moved
    # beside a freshly built EXE.
    for item in copied:
        item["path"] = str(Path(str(item["path"])).relative_to(destination))
    manifest = {
        "schema": KIT_SCHEMA,
        "name": KIT_NAME,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "purpose": "local-offline-android-verification-only",
        "source": {"sdk": str(sdk), "imageSdk": str(image_sdk), "javaHome": str(java_home), "buildTools": tools.name},
        "apis": list(APIS),
        "abi": "x86_64",
        "files": copied,
        "redistribution": "Google Android SDK/system images are not to be published from this repository or a public competition archive. Build this kit only from components already accepted/downloaded by the local licence holder.",
    }
    (destination / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (destination / "LOCAL_USE_ONLY.txt").write_text(
        "This directory was assembled from locally installed SDK/runtime components.\n"
        "Do not commit, upload or redistribute it. Rebuild it on the licence holder's PC.\n",
        encoding="utf-8",
    )
    return manifest


def verify_kit(root: Path) -> dict[str, object]:
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        return {"ok": False, "reason": "缺少 manifest.json"}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "reason": f"清单不可读：{exc}"}
    errors: list[str] = []
    for item in manifest.get("files", []):
        path = root / str(item.get("path", "")).replace("\\", "/")
        if not path.is_file():
            errors.append(f"缺失：{path}")
        elif _sha256(path) != item.get("sha256"):
            errors.append(f"哈希不匹配：{path}")
    sdk = root / "android-sdk"
    for api in APIS:
        if not (sdk / "system-images" / f"android-{api}" / "default" / "x86_64" / "system.img").is_file():
            errors.append(f"缺少 API {api} 镜像")
    if not (root / "jre" / "bin" / "java.exe").is_file():
        errors.append("缺少内置 Java")
    return {"ok": not errors, "errors": errors, "files": len(manifest.get("files", [])), "root": str(root)}


def main() -> int:
    parser = argparse.ArgumentParser(description="组装或校验 Lazarus Agent 本机离线 Android 资源包")
    parser.add_argument("command", choices=("build", "verify", "where"))
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent / "dist" / KIT_NAME)
    parser.add_argument("--sdk", type=Path)
    parser.add_argument("--image-sdk", type=Path, help="提供 API 34/35/36 default 镜像的 SDK（可与工具 SDK 分离）")
    parser.add_argument("--java-home", type=Path)
    args = parser.parse_args()
    if args.command == "where":
        print(json.dumps({"kit": str(default_kit_root()), "sdk": str(discover_sdk() or ""), "javaHome": str(discover_java_home() or "")}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "verify":
        result = verify_kit(args.out)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 2
    sdk = args.sdk or discover_sdk()
    java = args.java_home or discover_java_home()
    if not sdk or not java:
        raise SystemExit("未找到 SDK 或含 keytool 的 Java；请显式传 --sdk / --java-home")
    result = build_kit(args.out, sdk, java, args.image_sdk)
    print(json.dumps({"ok": True, "out": str(args.out), "files": len(result["files"]), "apis": result["apis"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
