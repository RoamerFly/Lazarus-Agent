"""用户可写目录的统一入口。

程序资源（Python、前端、内置工具）与运行产生的数据必须分离：安装版默认写入
用户数据目录，便携版在程序目录放置 ``portable.flag`` 后把数据保存在同级目录。
该模块也负责从早期 LegacyApkReviver 目录作一次只复制、不删除的任务迁移。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Mapping


APP_NAME = "Lazarus-Agent"
LEGACY_APP_NAME = "LegacyApkReviver"
STORAGE_ROOT_ENV = "LAZARUS_STORAGE_ROOT"
PORTABLE_ENV = "LAZARUS_PORTABLE"
PORTABLE_ROOT_ENV = "LAZARUS_PORTABLE_ROOT"
MIGRATE_LEGACY_ENV = "LAZARUS_MIGRATE_LEGACY"


def _absolute(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"存储目录必须为绝对路径：{path}")
    return path.resolve(strict=False)


def _program_root() -> Path:
    """返回 EXE 所在目录；开发态返回本模块所在的项目目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _default_installed_root(environ: Mapping[str, str]) -> Path:
    local_app_data = environ.get("LOCALAPPDATA")
    if local_app_data:
        return _absolute(local_app_data) / APP_NAME
    xdg_data = environ.get("XDG_DATA_HOME")
    if xdg_data:
        return _absolute(xdg_data) / APP_NAME
    return Path.home() / ".local" / "share" / APP_NAME


@dataclass(frozen=True)
class StoragePaths:
    """统一的用户可写目录集合；所有属性均为绝对路径。"""

    root: Path
    legacy_layout: bool = False

    @property
    def data(self) -> Path:
        # The first shipped version used root/jobs and root/desktop.log.  Keep
        # that live layout in place rather than copying multi-GB task data.
        return self.root if self.legacy_layout else self.root / "data"

    @property
    def logs(self) -> Path:
        return self.root if self.legacy_layout else self.data / "logs"

    @property
    def config(self) -> Path:
        return self.data / "config"

    @property
    def jobs(self) -> Path:
        return self.root / "jobs" if self.legacy_layout else self.data / "jobs"

    @property
    def models(self) -> Path:
        return self.root / "models"

    @property
    def output(self) -> Path:
        return self.root / "output"

    @property
    def temp(self) -> Path:
        return self.root / "temp"

    @property
    def env(self) -> Path:
        return self.root / "env"

    @property
    def runtimes(self) -> Path:
        return self.root / "runtimes"

    def runtime(self, name: str) -> Path:
        """取得受控运行时目录，拒绝路径分隔符以避免越界。"""
        if not name or Path(name).name != name or name in {".", ".."}:
            raise ValueError("运行时名称必须是单个目录名")
        return self.runtimes / name

    def ensure(self) -> "StoragePaths":
        for directory in (
            self.data, self.logs, self.config, self.jobs, self.models,
            self.output, self.temp, self.env, self.runtimes,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        return self


def resolve_storage_paths(
    environ: Mapping[str, str] | None = None,
    *,
    program_root: Path | None = None,
) -> StoragePaths:
    """解析存储根，不创建目录，便于启动前和测试中安全调用。

    优先级：LAZARUS_STORAGE_ROOT > LAZARUS_PORTABLE_ROOT > 便携标记 > 新安装目录。
    若新目录尚未建立且检测到旧安装目录，则原地沿用旧目录而非复制。
    便携标记为 ``LAZARUS_PORTABLE=1`` 或程序目录中的 ``portable.flag``。
    """
    env = os.environ if environ is None else environ
    root_from_env = env.get(STORAGE_ROOT_ENV)
    if root_from_env:
        root = _absolute(root_from_env)
        return StoragePaths(root, legacy_layout=root.name == LEGACY_APP_NAME and (root / "jobs").exists())

    program = _absolute(program_root or _program_root())
    portable_requested = env.get(PORTABLE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}
    portable_requested = portable_requested or (program / "portable.flag").is_file()
    if portable_requested:
        return StoragePaths(_absolute(env.get(PORTABLE_ROOT_ENV, program)))
    modern_root = _default_installed_root(env)
    # A build virtual environment may exist under the future storage root
    # before the user has ever run the new application.  It is not evidence
    # that user data was migrated, so do not let it hide a live legacy task
    # history (or the corresponding paired device and signing certificate).
    modern_has_user_data = any((modern_root / relative).exists() for relative in (
        Path("data/jobs"), Path("data/config"), Path("output"), Path("models"),
    ))
    if modern_root.exists() and modern_has_user_data:
        return StoragePaths(modern_root)
    old_root = legacy_root(env)
    if old_root and old_root.is_dir():
        return StoragePaths(old_root, legacy_layout=True)
    return StoragePaths(modern_root)


def legacy_root(environ: Mapping[str, str] | None = None) -> Path | None:
    """定位旧版目录；不存在 LOCALAPPDATA 的平台没有可迁移来源。"""
    env = os.environ if environ is None else environ
    value = env.get("LOCALAPPDATA")
    return (_absolute(value) / LEGACY_APP_NAME) if value else None


def _copy_missing(source: Path, destination: Path) -> list[str]:
    """递归复制 source 中 destination 尚不存在的文件，绝不覆盖目标。"""
    copied: list[str] = []
    if not source.exists():
        return copied
    if source.is_file():
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            copied.append(str(destination))
        return copied
    for item in source.rglob("*"):
        relative = item.relative_to(source)
        target = destination / relative
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif item.is_file() and not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
            copied.append(str(target))
    return copied


def migrate_legacy_data(
    paths: StoragePaths,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """只复制旧版任务和日志，返回迁移记录；旧目录不会被改动或删除。"""
    paths.ensure()
    source_root = legacy_root(environ)
    marker = paths.config / "legacy-migration-v1.json"
    if marker.exists():
        try:
            return json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # 记录损坏时仍可安全地补齐缺失文件；不会覆盖已有文件。
            pass

    copied: list[str] = []
    if source_root and source_root.resolve(strict=False) != paths.root.resolve(strict=False):
        copied.extend(_copy_missing(source_root / "jobs", paths.jobs))
        copied.extend(_copy_missing(source_root / "desktop.log", paths.logs / "legacy-desktop.log"))
    result: dict[str, object] = {
        "schema": 1,
        "completedAt": datetime.now(timezone.utc).isoformat(),
        "source": str(source_root) if source_root else None,
        "copied": copied,
        "mode": "copy-only-no-overwrite",
    }
    marker.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def configure_environment(*, migrate_legacy: bool | None = None) -> StoragePaths:
    """创建标准目录并为旧模块导出兼容环境变量。

    调用方应在导入 workbench/reviver 等旧模块前调用本函数。已有用户显式设置的
    REVIVER_JOBS、REVIVER_LAB 不会被覆盖。
    """
    paths = resolve_storage_paths().ensure()
    os.environ.setdefault("REVIVER_STORAGE_ROOT", str(paths.root))
    os.environ.setdefault("REVIVER_JOBS", str(paths.jobs))
    os.environ.setdefault("REVIVER_TEMP_ROOT", str(paths.temp))
    os.environ.setdefault("LAZARUS_LOG_DIR", str(paths.logs))
    # Migration must be deliberate.  A historical jobs directory commonly
    # includes APKs, SDK downloads and evidence totalling many gigabytes.
    if migrate_legacy is None:
        migrate_legacy = os.environ.get(MIGRATE_LEGACY_ENV, "").strip().lower() in {"1", "true", "yes", "on"}
    if migrate_legacy and not paths.legacy_layout:
        migrate_legacy_data(paths)
    return paths


def jobs_dir() -> Path:
    """兼容旧 REVIVER_JOBS 覆盖，同时给新代码提供统一任务目录。"""
    configured = os.environ.get("REVIVER_JOBS")
    return _absolute(configured) if configured else resolve_storage_paths().jobs
