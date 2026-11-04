"""Build a signed APK after a bounded decoded-tree transformation.

This is intentionally separate from the compatibility patcher.  A feature
transform receives an Apktool-decoded directory, changes only the files it has
already inspected, and returns a small audit record.  The result is rebuilt,
aligned and signed as a new APK.  No tool or decoded source is shipped in the
desktop executable.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Mapping, Any
from urllib.request import ProxyHandler, Request, build_opener, urlopen
from zipfile import BadZipFile, ZipFile

from offline_kit import discover_java_home, java_environment
from reviver import _tool_temp_root, run, test_key
from storage_paths import resolve_storage_paths


APKTOOL_VERSION = "2.12.1"
APKTOOL_URL = (
    "https://github.com/iBotPeaches/Apktool/releases/download/"
    f"v{APKTOOL_VERSION}/apktool_{APKTOOL_VERSION}.jar"
)
# The official GitHub release asset digest.  Keep a pinned release rather than
# accepting a jar found on PATH or in a mutable project directory.
APKTOOL_SHA256 = "66cf4524a4a45a7f56567d08b2c9b6ec237bcdd78cee69fd4a59c8a0243aeafa"
APKTOOL_SIZE = 25_926_183
MAX_APKTOOL_BYTES = 64 * 1024 * 1024
_LOCK_WAIT_SECONDS = 45


class ApkEditBuildError(RuntimeError):
    """A safe, user-presentable editing/build failure."""


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _apktool_cache() -> Path:
    return resolve_storage_paths().ensure().runtime("apktool") / f"apktool_{APKTOOL_VERSION}.jar"


def _validate_apktool(path: Path) -> bool:
    return (path.is_file() and path.stat().st_size == APKTOOL_SIZE and
            _sha256(path) == APKTOOL_SHA256)


def _download_apktool(destination: Path) -> None:
    """Fetch one pinned Apktool release, checking it before publish."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if APKTOOL_SIZE > MAX_APKTOOL_BYTES:
        raise ApkEditBuildError("Apktool 固定资产大小超出安全限制")
    chunk_size = 1024 * 1024
    ranges = [(start, min(start + chunk_size, APKTOOL_SIZE) - 1)
              for start in range(0, APKTOOL_SIZE, chunk_size)]

    # Some corporate/campus proxy PAC configurations terminate GitHub range
    # responses midway.  Keep the system proxy as the normal choice, but let a
    # knowledgeable user explicitly request a direct HTTPS connection.  This
    # does not alter process-wide proxy state or relax the pinned SHA check.
    direct = os.environ.get("REVIVER_APKTOOL_DIRECT", "").strip().lower() in {"1", "true", "yes"}
    opener = build_opener(ProxyHandler({})) if direct else None

    def fetch(staging: Path, start: int, end: int) -> None:
        expected = end - start + 1
        target = staging / ("part-%08d" % start)
        for attempt in range(3):
            request = Request(APKTOOL_URL, headers={
                "User-Agent": "Lazarus-Agent/0.4", "Range": "bytes=%d-%d" % (start, end),
            })
            try:
                open_request = opener.open if opener is not None else urlopen
                with open_request(request, timeout=45) as response:
                    content_range = response.headers.get("Content-Range")
                    if content_range != "bytes %d-%d/%d" % (start, end, APKTOOL_SIZE):
                        raise ApkEditBuildError("Apktool 下载服务器未返回预期分段")
                    data = response.read(expected + 1)
                if len(data) != expected:
                    raise ApkEditBuildError("Apktool 下载分段长度不符")
                target.write_bytes(data)
                return
            except ApkEditBuildError:
                raise
            except Exception as exc:
                target.unlink(missing_ok=True)
                if attempt == 2:
                    raise ApkEditBuildError("无法下载 Apktool 分段：" + type(exc).__name__) from exc
                time.sleep(0.4 * (attempt + 1))

    # The official CDN is slow on some campus networks.  Independently checked
    # range requests avoid a large unverified file in the managed cache.
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix=".apktool-download-") as temp:
        staging = Path(temp)
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(fetch, staging, start, end) for start, end in ranges]
            for future in futures:
                future.result()
        verified = staging / "apktool.jar"
        with verified.open("wb") as stream:
            for start, _end in ranges:
                with (staging / ("part-%08d" % start)).open("rb") as part:
                    shutil.copyfileobj(part, stream)
        if not _validate_apktool(verified):
            raise ApkEditBuildError("Apktool 校验失败，已拒绝使用下载文件")
        verified.replace(destination)


def apktool_jar() -> Path:
    """Return a hash-verified cached Apktool JAR, downloading only if absent."""
    explicit = os.environ.get("REVIVER_APKTOOL_JAR", "").strip()
    if explicit:
        candidate = Path(explicit)
        if not _validate_apktool(candidate):
            raise ApkEditBuildError("REVIVER_APKTOOL_JAR 不存在、版本不符或哈希未通过校验")
        return candidate.resolve(strict=True)
    destination = _apktool_cache()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if _validate_apktool(destination):
        return destination
    # A lock file makes concurrent desktop jobs wait for one checked download.
    lock = destination.with_suffix(".lock")
    deadline = time.monotonic() + _LOCK_WAIT_SECONDS
    while True:
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(descriptor, "w", encoding="ascii") as stream:
                stream.write("%d\n%.6f\n" % (os.getpid(), time.time()))
            try:
                if not _validate_apktool(destination):
                    destination.unlink(missing_ok=True)
                    _download_apktool(destination)
                return destination
            finally:
                lock.unlink(missing_ok=True)
        except FileExistsError:
            if _validate_apktool(destination):
                return destination
            # A workstation crash must not permanently block the downloader.
            # Never remove a young lock, since it can belong to another job.
            try:
                if time.time() - lock.stat().st_mtime > _LOCK_WAIT_SECONDS * 4:
                    lock.unlink()
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() >= deadline:
                raise ApkEditBuildError("等待 Apktool 准备超时")
            time.sleep(0.15)


def _java() -> str | Path:
    home = discover_java_home()
    if home is None:
        raise ApkEditBuildError("未找到可用 Java 运行时；请先准备 Android 工具包或设置 REVIVER_JAVA_HOME")
    candidate = home / "bin" / "java.exe"
    if not candidate.is_file():
        raise ApkEditBuildError("已配置 Java 运行时缺少 java.exe")
    return candidate


def _check_tools(tools: Path) -> tuple[Path, Path]:
    zipalign = tools / "zipalign.exe"
    signer = tools / "lib" / "apksigner.jar"
    if not zipalign.is_file() or not signer.is_file():
        raise ApkEditBuildError("Android build-tools 缺少 zipalign.exe 或 apksigner.jar")
    return zipalign, signer


def _copy_to_ascii(source: Path, destination: Path) -> None:
    if not source.is_file() or source.suffix.lower() != ".apk":
        raise ApkEditBuildError("请选择完整的单个 APK 文件")
    if source.stat().st_size <= 0 or source.stat().st_size > 320 * 1024 * 1024:
        raise ApkEditBuildError("APK 大小不在支持范围内")
    shutil.copyfile(source, destination)


def _validate_source_archive(source: Path) -> None:
    """Reject paths that could escape Apktool's temporary decode tree."""
    try:
        with ZipFile(source) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if len(entries) > 60_000 or len(names) != len(set(names)):
                raise ApkEditBuildError("APK 条目数量或名称不在支持范围内")
            if sum(entry.file_size for entry in entries) > 1024 * 1024 * 1024:
                raise ApkEditBuildError("APK 解包后的总大小超过限制")
            for entry in entries:
                name = entry.filename
                mode = (entry.external_attr >> 16) & 0o170000
                if (not name or "\x00" in name or "\\" in name or name.startswith("/") or
                        ":" in name or ".." in Path(name).parts or mode == 0o120000):
                    raise ApkEditBuildError("APK 包含不安全的归档路径或链接")
    except ApkEditBuildError:
        raise
    except BadZipFile as exc:
        raise ApkEditBuildError("APK 不是可安全读取的 ZIP 归档") from exc


def _validate_decoded_tree(decoded: Path) -> None:
    """Reject extracted links/reparse points before a transform reads them."""
    root = decoded.resolve(strict=True)
    for item in decoded.rglob("*"):
        attributes = getattr(item.stat(follow_symlinks=False), "st_file_attributes", 0)
        reparse = bool(attributes & 0x400)
        if item.is_symlink() or reparse:
            raise ApkEditBuildError("解码结果包含不支持的链接或重解析点")
        try:
            item.resolve(strict=False).relative_to(root)
        except ValueError as exc:
            raise ApkEditBuildError("解码结果路径越出临时目录") from exc


def inspect_decoded(source: Path, tools: Path, inspect: Callable[[Path], Mapping[str, Any]]) -> dict[str, Any]:
    """Run a read-only inspection against an isolated decoded APK tree.

    This is the shared entry point for feature planners such as string
    extraction.  The supplied callback gets no original path and cannot write
    outside the disposable decoded directory.  ``tools`` remains part of the
    interface so the caller can report the selected Android toolchain, but no
    build-tools binary is required for a read-only inspection.
    """
    source, tools = Path(source), Path(tools)
    if not callable(inspect):
        raise ApkEditBuildError("检查器不可用")
    if not source.is_file() or source.suffix.lower() != ".apk":
        raise ApkEditBuildError("请选择完整的单个 APK 文件")
    if source.stat().st_size <= 0 or source.stat().st_size > 320 * 1024 * 1024:
        raise ApkEditBuildError("APK 大小不在支持范围内")
    _validate_source_archive(source)
    jar = apktool_jar()
    env = java_environment(os.environ.copy())
    java = _java()
    with tempfile.TemporaryDirectory(dir=_tool_temp_root(), prefix="inspect-") as directory:
        work = Path(directory)
        original, decoded, framework = work / "source.apk", work / "decoded", work / "framework"
        _copy_to_ascii(source, original)
        framework.mkdir()
        try:
            run([java, "-jar", jar, "d", "--force", "--frame-path", framework,
                 "--output", decoded, original], env, work)
            _validate_decoded_tree(decoded)
            result = dict(inspect(decoded))
        except ApkEditBuildError:
            raise
        except Exception as exc:
            raise ApkEditBuildError("APK 结构检查失败：" + str(exc)[:800]) from exc
    return {"source_sha256": _sha256(source), "apktool": {"version": APKTOOL_VERSION,
            "sha256": APKTOOL_SHA256}, "inspection": result}


def build_modified(
    source: Path,
    output: Path,
    api: int,
    tools: Path,
    transform: Callable[[Path], Mapping[str, Any]],
) -> dict[str, Any]:
    """Decode, transform, rebuild and sign an APK as one atomic operation.

    The transform must return a JSON-like mapping with a non-empty ``kind``.
    Its audit record is embedded in the returned report only.  It never makes
    executable choices or receives model-controlled paths.
    """
    source, output, tools = Path(source), Path(output), Path(tools)
    if not callable(transform):
        raise ApkEditBuildError("修改器不可用")
    if source.resolve(strict=False) == output.resolve(strict=False) or output.exists():
        raise ApkEditBuildError("请为改造产物选择一个尚不存在的新文件")
    if not isinstance(api, int) or not 1 <= api <= 100:
        raise ApkEditBuildError("目标 Android API 无效")
    # Validate the user selected source before any runtime preparation.
    if not source.is_file() or source.suffix.lower() != ".apk":
        raise ApkEditBuildError("请选择完整的单个 APK 文件")
    if source.stat().st_size <= 0 or source.stat().st_size > 320 * 1024 * 1024:
        raise ApkEditBuildError("APK 大小不在支持范围内")
    _validate_source_archive(source)
    zipalign, signer = _check_tools(tools)
    jar = apktool_jar()
    output.parent.mkdir(parents=True, exist_ok=True)
    source_hash = _sha256(source)
    # Apktool and Android command tools receive only ASCII temporary names.
    with tempfile.TemporaryDirectory(dir=_tool_temp_root(), prefix="edit-") as directory:
        work = Path(directory)
        original = work / "source.apk"
        decoded, framework = work / "decoded", work / "framework"
        unsigned, aligned, signed = work / "unsigned.apk", work / "aligned.apk", work / "signed.apk"
        _copy_to_ascii(source, original)
        env = java_environment(os.environ.copy())
        java = _java()
        try:
            framework.mkdir()
            run([java, "-jar", jar, "d", "--force", "--frame-path", framework,
                 "--output", decoded, original], env, work)
            _validate_decoded_tree(decoded)
            audit = dict(transform(decoded))
            if not isinstance(audit.get("kind"), str) or not audit["kind"].strip():
                raise ApkEditBuildError("修改器未返回可审计的改造类型")
            _validate_decoded_tree(decoded)
            run([java, "-jar", jar, "b", "--frame-path", framework, decoded, "--output", unsigned], env, work)
            run([zipalign, "-p", "-f", "4", unsigned, aligned], env, work)
            key = test_key(env)
            # Do not force the selected verification target into signing.  The
            # original manifest's minSdk lets apksigner retain schemes required
            # by older devices; verification below remains target-specific.
            run([java, "-jar", signer, "sign", "--ks", key,
                 "--ks-pass", "env:REVIVER_KEYPASS", "--out", signed, aligned], env, work)
            run([java, "-jar", signer, "verify", "--min-sdk-version", str(api), "--verbose", signed], env, work)
        except ApkEditBuildError:
            raise
        except Exception as exc:
            raise ApkEditBuildError("APK 改造构建失败：" + str(exc)[:800]) from exc
        digest = _sha256(signed)
        with tempfile.TemporaryDirectory(dir=output.parent, prefix="publish-") as publish:
            staged = Path(publish) / "candidate.apk"
            shutil.copyfile(signed, staged)
            if _sha256(staged) != digest:
                raise ApkEditBuildError("候选 APK 发布前哈希不一致")
            staged.replace(output)
    return {
        "kind": audit.pop("kind"), "source_sha256": source_hash, "sha256": digest,
        "candidate": output.name, "api": api, "apktool": {"version": APKTOOL_VERSION, "sha256": APKTOOL_SHA256},
        "transform": audit,
    }
