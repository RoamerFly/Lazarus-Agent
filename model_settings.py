"""Persisted, endpoint-bound model settings for the local workbench.

Only the endpoint and model name are stored as ordinary JSON.  On Windows an
API key is protected for the current Windows user with DPAPI.  Other systems
may use the existing process environment but never write an API key to disk.
"""
from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import threading
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

from storage_paths import resolve_storage_paths

CONFIG_NAME = "model-connection-v1.json"
MAX_TEXT = 512
_CONFIG_LOCK = threading.RLock()


class ModelSettingsError(ValueError):
    """A configuration error safe to present in the local UI."""


def _config_path() -> Path:
    return resolve_storage_paths().ensure().config / CONFIG_NAME


def _valid_text(value: object, field: str, *, required: bool = True) -> str:
    if not isinstance(value, str):
        raise ModelSettingsError(field + "必须是文本")
    value = value.strip()
    if required and not value:
        raise ModelSettingsError(field + "不能为空")
    if len(value) > MAX_TEXT or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ModelSettingsError(field + "长度或内容无效")
    return value


def validate_url(value: object) -> str:
    value = _valid_text(value, "模型地址")
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError as exc:
        raise ModelSettingsError("模型地址或端口格式无效") from exc
    if any(char.isspace() for char in value) or "\\" in value:
        raise ModelSettingsError("模型地址包含无效字符")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ModelSettingsError("模型地址不能包含账号、查询参数或片段")
    if parsed.scheme == "https" and parsed.hostname:
        return value.rstrip("/")
    if parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
        return value.rstrip("/")
    raise ModelSettingsError("模型地址需为 HTTPS，或本机 HTTP 地址")


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _crypt(value: bytes, protect: bool) -> bytes:
    if os.name != "nt":
        raise ModelSettingsError("当前系统不支持把 API Key 持久化，请使用环境变量")
    source = (ctypes.c_byte * len(value)).from_buffer_copy(value)
    source_blob = _Blob(len(value), source)
    output = _Blob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    flags = 0x1  # CRYPTPROTECT_UI_FORBIDDEN
    if protect:
        ok = crypt32.CryptProtectData(ctypes.byref(source_blob), "Lazarus Agent model key", None, None, None, flags, ctypes.byref(output))
    else:
        ok = crypt32.CryptUnprotectData(ctypes.byref(source_blob), None, None, None, None, flags, ctypes.byref(output))
    if not ok:
        raise ModelSettingsError("无法读取 Windows 凭据保护存储")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)


def _load_saved() -> dict:
    with _CONFIG_LOCK:
        path = _config_path()
        if not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("schema") != 1:
                return {}
            return payload
        except (OSError, json.JSONDecodeError):
            return {}


def _stored_key(saved: dict) -> str:
    encoded = saved.get("protected_key")
    if not isinstance(encoded, str) or not encoded:
        return ""
    try:
        return _crypt(base64.b64decode(encoded, validate=True), False).decode("utf-8")
    except (ValueError, UnicodeError, ModelSettingsError):
        return ""


def get_config(environ: dict | None = None) -> dict:
    """Return effective endpoint configuration without borrowing a foreign key.

    A complete URL/model environment pair is a deliberate process-only
    override.  A GUI-saved URL/model otherwise wins.  A standalone
    ``REVIVER_MODEL_KEY`` is deliberately ignored for a saved URL because it
    has no endpoint binding and could leak a key to a newly edited provider.
    """
    env = os.environ if environ is None else environ
    saved = _load_saved()
    environment_override = bool(env.get("REVIVER_MODEL_URL") and env.get("REVIVER_MODEL"))
    raw_url = env.get("REVIVER_MODEL_URL") if environment_override else saved.get("url", "")
    raw_model = env.get("REVIVER_MODEL") if environment_override else saved.get("model", "")
    if not raw_url or not raw_model:
        return {"url": "", "model": "", "key": "", "enabled": False, "source": "none"}
    try:
        url, model = validate_url(raw_url), _valid_text(raw_model, "模型名称")
    except ModelSettingsError:
        return {"url": "", "model": "", "key": "", "enabled": False, "source": "none"}
    environment_key = env.get("REVIVER_MODEL_KEY", "") if environment_override else ""
    key = _valid_text(environment_key, "API Key", required=False) if environment_key else ""
    if not environment_override and saved.get("url") == url and saved.get("model") == model:
        key = _stored_key(saved)
    return {"url": url, "model": model, "key": key,
            "enabled": bool(saved.get("enabled", True)) if not environment_override else True,
            "source": "environment" if environment_override else "saved"}


def public_config() -> dict:
    config = get_config()
    return {"url": config["url"], "model": config["model"], "enabled": config["enabled"],
            "has_key": bool(config["key"]), "source": config["source"],
            "key_storage": "windows-dpapi" if os.name == "nt" else "environment-only"}


def preview_config(body: dict) -> dict:
    """Validate an unsaved settings form for a connection test.

    It never writes a file.  Omitting ``key`` can reuse the DPAPI key only
    when the preview points at the exact same saved URL and model.  A supplied
    key is used only for this request and is not retained by this function.
    """
    if not isinstance(body, dict):
        raise ModelSettingsError("配置必须是对象")
    saved = _load_saved()
    url = validate_url(body.get("url", saved.get("url", "")))
    model = _valid_text(body.get("model", saved.get("model", "")), "模型名称")
    enabled = body.get("enabled", saved.get("enabled", True))
    if not isinstance(enabled, bool):
        raise ModelSettingsError("启用开关必须是布尔值")
    clear_key = body.get("clear_key", False)
    if not isinstance(clear_key, bool):
        raise ModelSettingsError("清除密钥开关必须是布尔值")
    if "key" in body:
        key = _valid_text(body["key"], "API Key", required=False)
        if key and clear_key:
            raise ModelSettingsError("不能同时保存和清除 API Key")
    else:
        key = ""
    if not key and not clear_key:
        effective = get_config()
        if (url, model) == (effective["url"], effective["model"]):
            key = effective["key"]
    return {"url": url, "model": model, "key": key, "enabled": enabled, "source": "preview"}


def save_config(body: dict) -> dict:
    """Atomically replace settings, serialising concurrent local UI requests."""
    with _CONFIG_LOCK:
        return _save_config_locked(body)


def _save_config_locked(body: dict) -> dict:
    if not isinstance(body, dict):
        raise ModelSettingsError("配置必须是对象")
    enabled = body.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ModelSettingsError("启用开关必须是布尔值")
    # A disabled empty setting is useful for explicitly clearing auto-repair
    # model use on a shared device.  Existing endpoint details are retained
    # only when callers omit them entirely.
    saved = _load_saved()
    url_value = body.get("url", saved.get("url", ""))
    model_value = body.get("model", saved.get("model", ""))
    if not url_value and not model_value and not enabled:
        url, model = "", ""
    else:
        url = validate_url(url_value)
        model = _valid_text(model_value, "模型名称")
    key = _valid_text(body.get("key", ""), "API Key", required=False)
    clear_key = body.get("clear_key", False)
    if not isinstance(clear_key, bool):
        raise ModelSettingsError("清除密钥开关必须是布尔值")
    if clear_key and key:
        raise ModelSettingsError("不能同时保存和清除 API Key")
    payload = {"schema": 1, "url": url, "model": model, "enabled": enabled}
    # A changed endpoint/model deliberately drops the old credential. This
    # prevents cross-provider credential reuse.
    if key:
        if os.name != "nt":
            raise ModelSettingsError("当前系统请通过 REVIVER_MODEL_KEY 设置 API Key")
        payload["protected_key"] = base64.b64encode(_crypt(key.encode("utf-8"), True)).decode("ascii")
    elif not clear_key and saved.get("url") == url and saved.get("model") == model and saved.get("protected_key"):
        payload["protected_key"] = saved["protected_key"]
    with _CONFIG_LOCK:
        path = _config_path()
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    return public_config()


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ModelSettingsError("模型端点重定向已拒绝")


def safe_urlopen(request: Request, timeout: int = 15):
    """Open a configured model request while rejecting all HTTP redirects."""
    return build_opener(_NoRedirect()).open(request, timeout=timeout)


def test_connection(config: dict | None = None) -> dict:
    """Confirm a compatible endpoint accepts a bounded function-call request."""
    effective = get_config() if config is None else config
    if config is None and not effective.get("enabled"):
        raise ModelSettingsError("请先配置模型地址和模型名称")
    url, model = validate_url(effective.get("url")), _valid_text(effective.get("model"), "模型名称")
    key = _valid_text(effective.get("key", ""), "API Key", required=False)
    payload = {"model": model, "messages": [{"role": "user", "content": "调用 inspect_repair_evidence 工具。"}],
               "tools": [{"type": "function", "function": {"name": "inspect_repair_evidence", "description": "读取脱敏 APK 诊断摘要", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}}],
               "tool_choice": {"type": "function", "function": {"name": "inspect_repair_evidence"}}, "max_tokens": 80}
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    request = Request(url + "/chat/completions", data=json.dumps(payload).encode("utf-8"), headers=headers)
    try:
        with safe_urlopen(request, timeout=15) as response:
            result = json.loads(response.read(128 * 1024))
        calls = result["choices"][0]["message"].get("tool_calls") or []
        if not any(isinstance(call, dict) and call.get("function", {}).get("name") == "inspect_repair_evidence" for call in calls):
            raise ModelSettingsError("该端点未返回所需的函数调用能力")
    except ModelSettingsError:
        raise
    except Exception as exc:
        raise ModelSettingsError("模型连接或函数调用测试失败：" + type(exc).__name__) from exc
    return {"connected": True, "model": model, "function_calling": True}
