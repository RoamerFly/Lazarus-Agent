"""FastAPI routes for the desktop About and signed-update dialog.

Mount this router from ``workbench.py`` after the FastAPI app exists:
``app.include_router(updates_router)``. It deliberately owns no APK repair
state; download jobs contain only the signed desktop release artifact.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import uuid

from fastapi import APIRouter, HTTPException

from app_version import APP_NAME, APP_VERSION, DEVELOPER_NAME, DEVELOPER_URL, ISSUES_URL, REPOSITORY_URL
from storage_paths import configure_environment
from updater import (
    DownloadProgress, UpdateCancelled, UpdateError, check_for_update, download_asset,
    ignore_version, ignored_version, preferred_asset_kind, verify_asset_file,
)


router = APIRouter(prefix="/api/updates", tags=["updates"])
_cached: dict[str, object] = {}
_downloads: dict[str, "DownloadJob"] = {}
_downloads_lock = threading.Lock()


@dataclass
class DownloadJob:
    manifest: object
    kind: str
    cancel_event: threading.Event = field(default_factory=threading.Event)
    status: str = "queued"
    downloaded: int = 0
    total: int | None = None
    error: str | None = None
    artifact: Path | None = None

    def progress(self, event: DownloadProgress) -> None:
        self.downloaded, self.total = event.downloaded, event.total

    def response(self) -> dict[str, object]:
        return {
            "status": self.status, "downloaded": self.downloaded, "total": self.total,
            "percent": None if not self.total else min(100, int(self.downloaded * 100 / self.total)),
            "error": self.error, "ready": self.status == "ready", "cancelled": self.status == "cancelled",
        }


def _serialize(manifest, *, startup: bool = False) -> dict[str, object]:
    ignored = bool(manifest and ignored_version() == manifest.version)
    return {
        "available": bool(manifest) and not (startup and ignored), "ignored": ignored,
        "current_version": APP_VERSION, "version": manifest.version if manifest else APP_VERSION,
        "published_at": manifest.published_at if manifest else "", "notes": manifest.notes if manifest else "",
        "assets": sorted(manifest.assets) if manifest else [],
        "recommended_asset": preferred_asset_kind() if manifest else None,
    }


def _require_manifest():
    manifest = _cached.get("manifest")
    if manifest is None:
        try:
            manifest = check_for_update()
        except UpdateError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
    if manifest is None:
        raise HTTPException(status_code=409, detail="当前已是最新版本。")
    _cached["manifest"] = manifest
    return manifest


@router.get("/about")
def about() -> dict[str, str]:
    return {"name": APP_NAME, "version": APP_VERSION, "developer": DEVELOPER_NAME, "developer_url": DEVELOPER_URL,
            "repository_url": REPOSITORY_URL, "issues_url": ISSUES_URL,
            "platform": "Windows" if os.name == "nt" else "unsupported"}


@router.post("/check")
def check(startup: bool = False) -> dict[str, object]:
    try:
        manifest = check_for_update()
    except UpdateError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    _cached["manifest"] = manifest
    return _serialize(manifest, startup=startup)


@router.post("/ignore")
def ignore(version: str) -> dict[str, object]:
    manifest = _require_manifest()
    if version != manifest.version:
        raise HTTPException(status_code=400, detail="只能忽略当前已验证的更新版本。")
    ignore_version(version)
    return {"ignored": version}


def _run_download(job: DownloadJob) -> None:
    job.status = "downloading"
    try:
        artifact = download_asset(job.manifest, job.kind, progress=job.progress, cancel_event=job.cancel_event)
        verify_asset_file(job.manifest, job.kind, artifact)
        job.artifact, job.status = artifact, "ready"
    except UpdateCancelled as error:
        job.error, job.status = str(error), "cancelled"
    except UpdateError as error:
        job.error, job.status = str(error), "error"
    except Exception:
        job.error, job.status = "更新包下载失败。", "error"


@router.post("/download")
def download(kind: str | None = None) -> dict[str, object]:
    manifest = _require_manifest()
    selected = kind or preferred_asset_kind()
    if selected not in manifest.assets:
        raise HTTPException(status_code=400, detail="此版本没有请求的 Windows 安装包。")
    job = DownloadJob(manifest=manifest, kind=selected)
    job_id = uuid.uuid4().hex
    with _downloads_lock:
        _downloads[job_id] = job
    threading.Thread(target=_run_download, args=(job,), daemon=True, name=f"update-download-{job_id[:8]}").start()
    return {"id": job_id, "kind": selected, **job.response()}


@router.get("/download/{job_id}")
def download_status(job_id: str) -> dict[str, object]:
    with _downloads_lock:
        job = _downloads.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="更新下载任务不存在或已过期。")
    return job.response()


@router.post("/download/{job_id}/cancel")
def cancel_download(job_id: str) -> dict[str, object]:
    with _downloads_lock:
        job = _downloads.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="更新下载任务不存在或已过期。")
    if job.status not in {"ready", "error", "cancelled"}:
        job.cancel_event.set()
    return {"requested": True, **job.response()}


@router.post("/download/{job_id}/use")
def use_download(job_id: str) -> dict[str, object]:
    with _downloads_lock:
        job = _downloads.get(job_id)
    if job is None or job.status != "ready" or not job.artifact:
        raise HTTPException(status_code=409, detail="更新包尚未准备完成。")
    _cached.update(manifest=job.manifest, artifact=job.artifact, kind=job.kind)
    return {"ready": True}


def _completed_artifact() -> tuple[object, str, Path]:
    manifest, artifact, kind = _cached.get("manifest"), _cached.get("artifact"), _cached.get("kind")
    if not isinstance(artifact, Path) or not artifact.is_file() or not isinstance(kind, str) or manifest is None:
        raise HTTPException(status_code=409, detail="请先完成已签名更新包下载。")
    try:
        verify_asset_file(manifest, kind, artifact)
    except UpdateError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return manifest, kind, artifact


@router.post("/install")
def install() -> dict[str, object]:
    manifest, kind, artifact = _completed_artifact()
    if not getattr(sys, "frozen", False):
        raise HTTPException(status_code=409, detail="开发态不支持覆盖当前 Python 启动器。")
    mode = "installer" if kind == "windows-x64-installer" else "portable"
    if mode == "portable" and kind != "windows-x64-portable-exe":
        raise HTTPException(status_code=400, detail="未知更新包类型。")
    # A Windows executable remains locked while it is the process image. Run a
    # trusted byte-for-byte copy from user temp instead, so the original target
    # can be replaced after this desktop process exits.
    helper_dir = configure_environment().temp / "update-helper" / uuid.uuid4().hex
    helper_dir.mkdir(parents=True, exist_ok=False)
    helper = helper_dir / "Lazarus-Agent-update-helper.exe"
    shutil.copy2(sys.executable, helper)
    expected = manifest.assets[kind].sha256
    try:
        subprocess.Popen([str(helper), "--apply-update", "--mode", mode, "--source", str(artifact),
                          "--source-sha256", expected, "--target", sys.executable,
                          "--parent-pid", str(os.getpid()), "--version", manifest.version], close_fds=True)
    except OSError as error:
        raise HTTPException(status_code=500, detail="无法启动独立更新进程。") from error
    return {"started": True, "close_app": True, "message": "请关闭工作台，更新完成后会自动重新启动。"}
