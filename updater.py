"""Signed Windows update manifest handling.

The updater deliberately trusts only a detached Ed25519 signature over the
manifest and a SHA-256 digest of the selected release artifact.  GitHub is a
transport, not a trust boundary: a malformed or unsigned release is ignored.
"""
from __future__ import annotations

from dataclasses import dataclass
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import threading
from typing import Any, Callable
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app_version import APP_VERSION
from storage_paths import configure_environment


MANIFEST_URL = "https://github.com/RoamerFly/Lazarus-Agent/releases/latest/download/latest.json"
MANIFEST_SIGNATURE_URL = "https://github.com/RoamerFly/Lazarus-Agent/releases/latest/download/latest.json.sig"
_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_HEX_256 = re.compile(r"^[a-f0-9]{64}$")


class UpdateError(RuntimeError):
    """An update could not be authenticated or safely prepared."""


class UpdateCancelled(UpdateError):
    """The user cancelled a streaming update download."""


@dataclass(frozen=True)
class UpdateAsset:
    name: str
    url: str
    sha256: str


@dataclass(frozen=True)
class UpdateManifest:
    version: str
    published_at: str
    notes: str
    assets: dict[str, UpdateAsset]


@dataclass(frozen=True)
class DownloadProgress:
    downloaded: int
    total: int | None

    @property
    def percent(self) -> int | None:
        return None if not self.total else min(100, int(self.downloaded * 100 / self.total))


def _resource_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


def public_key_path() -> Path:
    return _resource_root() / "update" / "lazarus-update-public.pem"


def _version_tuple(value: str) -> tuple[int, int, int]:
    matched = _SEMVER.fullmatch(value)
    if not matched:
        raise UpdateError("更新清单的版本号不是受支持的 SemVer 格式。")
    return tuple(int(item) for item in matched.groups())


def _safe_https(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and bool(parsed.netloc) and not parsed.username and not parsed.password


def verify_manifest(manifest_bytes: bytes, signature_bytes: bytes, key_file: Path | None = None) -> None:
    """Verify base64 detached signature before JSON is interpreted."""
    key_file = key_file or public_key_path()
    try:
        key = serialization.load_pem_public_key(key_file.read_bytes())
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError("更新公钥必须是 Ed25519")
        signature = base64.b64decode(signature_bytes.strip(), validate=True)
        key.verify(signature, manifest_bytes)
    except (OSError, ValueError, TypeError, InvalidSignature) as error:
        raise UpdateError("更新清单签名校验失败，已拒绝更新。") from error


def parse_manifest(manifest_bytes: bytes) -> UpdateManifest:
    try:
        document: dict[str, Any] = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise UpdateError("更新清单不是有效 JSON。") from error
    if document.get("schema") != 1:
        raise UpdateError("不支持的更新清单版本。")
    version = str(document.get("version", ""))
    _version_tuple(version)
    raw_assets = document.get("assets")
    if not isinstance(raw_assets, dict) or not raw_assets:
        raise UpdateError("更新清单没有可用的 Windows 安装包。")
    assets: dict[str, UpdateAsset] = {}
    for kind, raw in raw_assets.items():
        if not isinstance(kind, str) or not isinstance(raw, dict):
            raise UpdateError("更新清单的安装包字段无效。")
        name, url, digest = raw.get("name"), raw.get("url"), raw.get("sha256")
        if not isinstance(name, str) or Path(name).name != name or not name:
            raise UpdateError("更新清单包含不安全的文件名。")
        if not isinstance(url, str) or not _safe_https(url):
            raise UpdateError("更新清单包含不安全的下载地址。")
        if not isinstance(digest, str) or not _HEX_256.fullmatch(digest.lower()):
            raise UpdateError("更新清单缺少有效 SHA-256。")
        assets[kind] = UpdateAsset(name=name, url=url, sha256=digest.lower())
    return UpdateManifest(
        version=version,
        published_at=str(document.get("published_at", "")),
        notes=str(document.get("notes", "")),
        assets=assets,
    )


def _get(url: str) -> bytes:
    if not _safe_https(url):
        raise UpdateError("仅允许 HTTPS 更新地址。")
    request = Request(url, headers={"User-Agent": f"Lazarus-Agent/{APP_VERSION}", "Accept": "application/json"})
    try:
        with urlopen(request, timeout=12) as response:
            if response.status != 200:
                raise UpdateError(f"更新服务器返回 HTTP {response.status}。")
            return response.read()
    except (OSError, URLError) as error:
        raise UpdateError("暂时无法连接更新服务。") from error


def check_for_update() -> UpdateManifest | None:
    """Return a verified newer release, or None when already current."""
    manifest_bytes = _get(MANIFEST_URL)
    signature = _get(MANIFEST_SIGNATURE_URL)
    verify_manifest(manifest_bytes, signature)
    manifest = parse_manifest(manifest_bytes)
    return manifest if _version_tuple(manifest.version) > _version_tuple(APP_VERSION) else None


def _download_directory(version: str) -> Path:
    paths = configure_environment()
    return paths.temp / "updates" / version


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while block := file.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def verify_asset_file(manifest: UpdateManifest, kind: str, path: Path) -> None:
    """Re-check a previously downloaded file immediately before launching it."""
    asset = manifest.assets.get(kind)
    if asset is None or not path.is_file():
        raise UpdateError("更新包不存在或不属于已验证的更新清单。")
    try:
        digest = _sha256_file(path)
    except OSError as error:
        raise UpdateError("无法读取已下载的更新包。") from error
    if digest != asset.sha256:
        raise UpdateError("更新包在下载后发生变化，已拒绝安装。")


def update_notice_file() -> Path:
    return configure_environment().config / "update-notices.json"


def ignored_version(notice_file: Path | None = None) -> str | None:
    try:
        document = json.loads((notice_file or update_notice_file()).read_text(encoding="utf-8"))
        value = document.get("ignored_version")
        return value if isinstance(value, str) and _SEMVER.fullmatch(value) else None
    except (OSError, json.JSONDecodeError):
        return None


def ignore_version(version: str, notice_file: Path | None = None) -> None:
    """Suppress only this exact release; the next version will be announced."""
    _version_tuple(version)
    target = notice_file or update_notice_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps({"schema": 1, "ignored_version": version}, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, target)


def download_asset(
    manifest: UpdateManifest,
    kind: str,
    *,
    progress: Callable[[DownloadProgress], None] | None = None,
    cancel_event: threading.Event | None = None,
    destination: Path | None = None,
) -> Path:
    """Download an authenticated asset to user storage, verify it, then atomically publish it."""
    if kind not in manifest.assets:
        raise UpdateError("此版本没有请求的 Windows 安装包。")
    asset = manifest.assets[kind]
    target_dir = destination or _download_directory(manifest.version)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / asset.name
    if target.is_file():
        try:
            if _sha256_file(target) == asset.sha256:
                if progress:
                    progress(DownloadProgress(target.stat().st_size, target.stat().st_size))
                return target
        except OSError:
            pass
    temporary = target.with_suffix(target.suffix + ".part")
    try:
        request = Request(asset.url, headers={"User-Agent": f"Lazarus-Agent/{APP_VERSION}"})
        with urlopen(request, timeout=30) as response, temporary.open("wb") as output:
            if response.status != 200:
                raise UpdateError(f"安装包下载失败（HTTP {response.status}）。")
            raw_total = response.headers.get("Content-Length")
            total = int(raw_total) if raw_total and raw_total.isdecimal() else None
            digest = hashlib.sha256()
            downloaded = 0
            while chunk := response.read(1024 * 1024):
                if cancel_event and cancel_event.is_set():
                    raise UpdateCancelled("已取消更新包下载。")
                digest.update(chunk)
                output.write(chunk)
                downloaded += len(chunk)
                if progress:
                    progress(DownloadProgress(downloaded, total))
        if cancel_event and cancel_event.is_set():
            raise UpdateCancelled("已取消更新包下载。")
        if digest.hexdigest() != asset.sha256:
            raise UpdateError("安装包 SHA-256 不匹配，已拒绝安装。")
        os.replace(temporary, target)
        return target
    except (OSError, URLError) as error:
        raise UpdateError("安装包下载失败。") from error
    finally:
        temporary.unlink(missing_ok=True)


def preferred_asset_kind() -> str:
    """Installed users receive the installer; an explicit portable app receives the EXE."""
    program = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    portable = os.environ.get("LAZARUS_PORTABLE", "").lower() in {"1", "true", "yes", "on"}
    return "windows-x64-portable-exe" if portable or (program / "portable.flag").is_file() else "windows-x64-installer"
