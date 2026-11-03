"""State helpers for evidence-backed multi-Android compatibility checks.

This module deliberately contains no ADB calls.  Keeping target parsing and
state transitions pure makes the HTTP contract testable without an emulator.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable


ROW_STATES = frozenset({"pending", "running", "passed", "failed", "unavailable","skipped"})


def normalize_targets(value: Any, allowed: Iterable[int], default: int) -> list[int]:
    """Validate an explicitly selected subset, preserving its UI order.

    Omitting ``targets`` is intentionally the old, single-target behaviour.
    A sparse selection such as ``[34, 36]`` is valid: the caller, rather than
    the backend, decides whether it means a compatibility range.
    """
    available = set(allowed)
    if value is None:
        return [default]
    if not isinstance(value, list) or not value:
        raise ValueError("targets 必须是至少包含一个 Android API 的数组")
    if any(isinstance(api, bool) or not isinstance(api, int) for api in value):
        raise ValueError("targets 中的 Android API 必须是整数")
    if len(value) != len(set(value)):
        raise ValueError("targets 不能重复")
    invalid = [api for api in value if api not in available]
    if invalid:
        raise ValueError("targets 包含未配置的 Android API：" + ", ".join(map(str, invalid)))
    return value.copy()


def new_matrix(targets: list[int], artifact_api: int) -> dict[str, Any]:
    """Create the durable progress shape returned from the job endpoint."""
    return {
        "artifact_api": artifact_api,
        "targets": targets.copy(),
        "status": "running",
        "rows": [{"api": api, "status": "pending"} for api in targets],
    }


def set_row(matrix: dict[str, Any], api: int, **values: Any) -> dict[str, Any]:
    """Replace one matrix row and return it; an unknown API is a programming error."""
    for row in matrix["rows"]:
        if row["api"] == api:
            row.update(values)
            if row.get("status") not in ROW_STATES:
                raise ValueError("无效的兼容性验证状态")
            return row
    raise KeyError(f"API {api} 不在此兼容性验证矩阵中")


def finish_matrix(matrix: dict[str, Any], artifact: dict[str, Any] | None) -> bool:
    """Finalize only if every explicitly requested environment passed.

    An unavailable image is intentionally not a pass.  The returned boolean
    is the sole gate the workbench uses before exposing an export.
    """
    expected = artifact.get("sha256") if isinstance(artifact, dict) else None
    for row in matrix["rows"]:
        if row.get("status") == "passed" and row.get("installed_sha256") != expected:
            # A positive launch result cannot prove that the device ran the
            # same bytes the user is about to download.
            row.update(status="failed", failure="设备安装包哈希与待导出 APK 不一致")
    passed = bool(expected) and all(row.get("status") == "passed" for row in matrix["rows"])
    matrix["status"] = "passed" if passed else "failed"
    matrix["artifact"] = deepcopy(artifact) if passed else None
    return passed
