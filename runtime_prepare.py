"""Serialized, explicit-consent preparation of local Android verification runtimes."""
from __future__ import annotations

import json
from pathlib import Path
import threading
from typing import Callable
import uuid

from storage_paths import configure_environment
from tools.prepare_lab import inventory, prepare_runtime, managed_runtime_root

TARGETS = (34, 35, 36)
MAX_ACTIVE_EVENTS = 120


class RuntimePreparation:
    """Owns one background prepare operation and never starts it without consent.

    ``operation_lock`` is the workbench device/toolchain lock.  Keeping it for
    the whole preparation means a repair cannot use a half-published toolchain.
    """
    def __init__(self, operation_lock: threading.Lock | threading.RLock | None = None,
                 prepare: Callable = prepare_runtime, inspect: Callable = inventory, state_file: Path | None = None):
        self.operation_lock = operation_lock or threading.Lock()
        self.prepare, self.inspect = prepare, inspect
        self.guard = threading.RLock()
        self.cancel_event: threading.Event | None = None
        self.active: dict | None = None
        self.state_file = state_file or managed_runtime_root().parent / "runtime-prepare.json"

    def _terms_accepted(self) -> bool:
        try:
            return bool(json.loads(self.state_file.read_text(encoding="utf-8")).get("terms_accepted"))
        except (OSError, ValueError, TypeError):
            return False

    def _accept_terms(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_file.with_suffix(".tmp")
        temporary.write_text(json.dumps({"terms_accepted": True}, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.state_file)

    def status(self) -> dict:
        with self.guard:
            active = dict(self.active) if self.active else None
            if active:
                active["events"] = list(self.active.get("events", [])[-20:])
            return {"terms_accepted": self._terms_accepted(), "targets": {str(api): self.inspect(api) for api in TARGETS},
                    "active": active}

    def start(self, targets: list[int], accept_terms: bool) -> dict:
        if not isinstance(targets, list) or not targets or any(api not in TARGETS for api in targets):
            raise ValueError("请选择 Android 14、15 或 16 中的至少一个验证目标")
        targets = list(dict.fromkeys(targets))
        with self.guard:
            if self.active and self.active.get("status") == "running":
                return {"started": False, "active": dict(self.active), "message": "已有按需准备任务正在运行"}
            # Keep a completed/cancelled result visible until the next explicit
            # preparation request, then replace it with the new operation.
            self.active = None
            missing = [api for api in targets if not self.inspect(api)["ready"]]
            if not missing:
                return {"started": False, "ready": True, "message": "所选运行环境已完整可用，不会联网下载"}
            if not self._terms_accepted():
                if not accept_terms:
                    raise PermissionError("下载前需要由本机用户明确同意 Android SDK 与 Temurin 发布方条款")
                self._accept_terms()
            if not self.operation_lock.acquire(blocking=False):
                raise RuntimeError("当前正在修复或验证，请结束后再准备运行环境")
            cancel = threading.Event()
            self.cancel_event = cancel
            self.active = {"id": uuid.uuid4().hex, "status": "running", "targets": missing, "events": [], "current": None}
            worker = threading.Thread(target=self._run, args=(missing, cancel), daemon=True, name="LazarusRuntimePrepare")
            worker.start()
            return {"started": True, "active": dict(self.active)}

    def _run(self, targets: list[int], cancel: threading.Event) -> None:
        try:
            for api in targets:
                with self.guard:
                    if self.active: self.active["current"] = api
                def progress(event, target=api):
                    safe = {key: event.get(key) for key in ("phase", "component", "message", "api", "current", "total")}
                    safe["api"] = target
                    with self.guard:
                        if self.active:
                            events = self.active["events"]
                            events.append(safe)
                            # A system-image download reports each chunk. Keep
                            # bounded state in memory as well as bounded API
                            # responses, so a long run cannot grow forever.
                            if len(events) > MAX_ACTIVE_EVENTS:
                                del events[:-MAX_ACTIVE_EVENTS]
                self.prepare(api, progress=progress, cancel=cancel, terms_accepted=True)
            with self.guard:
                if self.active: self.active.update(status="ready", current=None, message="所选运行环境已准备完成")
        except InterruptedError as exc:
            with self.guard:
                if self.active: self.active.update(status="cancelled", current=None, message=str(exc))
        except Exception as exc:
            with self.guard:
                if self.active: self.active.update(status="error", current=None, message=str(exc)[:800])
        finally:
            self.operation_lock.release()
            with self.guard:
                self.cancel_event = None

    def cancel(self) -> dict:
        with self.guard:
            if not self.active or self.active.get("status") != "running" or not self.cancel_event:
                return {"cancelled": False, "message": "没有可取消的按需准备任务"}
            self.cancel_event.set()
            return {"cancelled": True, "message": "已请求取消；当前下载块结束后会停止"}
