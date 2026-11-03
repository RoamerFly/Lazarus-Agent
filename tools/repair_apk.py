#!/usr/bin/env python3
"""Run one verified APK-repair job through the local Reviver workbench HTTP API.

The workbench must already be running locally (for example: .\\start_workbench.ps1).
This client deliberately does not invoke ADB, modify the input APK, or call the
repair engine directly: it sends the same import and repair requests as the web UI.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen


ANDROID_TO_API = {14: 34, 15: 35, 16: 36}
EXIT_VERIFIED = 0
EXIT_NOT_EXPORTED = 10
EXIT_JOB_ERROR = 11
EXIT_HTTP_ERROR = 12
EXIT_OUTPUT_CONFLICT = 13
EXIT_TIMEOUT = 14
CHUNK_SIZE = 1024 * 1024


class ClientError(RuntimeError):
    pass


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def parse_local_base(value: str) -> tuple[str, int, str]:
    parsed = urlparse(value)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise ClientError("--workbench 只能是 http://127.0.0.1:<port> 或 localhost 本机工作台")
    if parsed.path.rstrip("/"):
        raise ClientError("--workbench 不应包含路径")
    return parsed.hostname, parsed.port or 80, f"{parsed.scheme}://{parsed.netloc}"


def read_json(base: str, path: str) -> dict[str, Any]:
    try:
        with urlopen(Request(base + path), timeout=30) as response:
            raw = response.read()
    except Exception as exc:  # urllib exception types differ by Python/runtime
        raise ClientError(f"工作台请求失败 {path}: {exc}") from exc
    try:
        value = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise ClientError(f"工作台返回的不是 JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ClientError(f"工作台 JSON 结构异常: {path}")
    return value


def response_json(connection: http.client.HTTPConnection, label: str) -> dict[str, Any]:
    response = connection.getresponse()
    raw = response.read()
    try:
        body = json.loads(raw.decode("utf-8")) if raw else {}
    except Exception as exc:
        raise ClientError(f"{label} 返回的不是 JSON（HTTP {response.status}）") from exc
    if response.status < 200 or response.status >= 300:
        detail = body.get("detail") if isinstance(body, dict) else None
        raise ClientError(f"{label} 失败（HTTP {response.status}）：{detail or body}")
    if not isinstance(body, dict):
        raise ClientError(f"{label} JSON 结构异常")
    return body


def stream_import(host: str, port: int, api: int, token: str, apk: Path) -> dict[str, Any]:
    """Upload a large APK without reading it all into memory."""
    length = apk.stat().st_size
    connection = http.client.HTTPConnection(host, port, timeout=120)
    try:
        connection.putrequest("POST", f"/api/import/{api}")
        connection.putheader("X-Reviver-Token", token)
        connection.putheader("Content-Length", str(length))
        connection.endheaders()
        with apk.open("rb") as source:
            while chunk := source.read(CHUNK_SIZE):
                connection.send(chunk)
        return response_json(connection, "导入")
    except OSError as exc:
        raise ClientError(f"上传 APK 时失败: {exc}") from exc
    finally:
        connection.close()


def post_json(base: str, path: str, token: str, body: dict[str, Any] | None) -> dict[str, Any]:
    encoded = b"" if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"X-Reviver-Token": token}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = Request(base + path, data=encoded, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read()
    except Exception as exc:
        raise ClientError(f"工作台请求失败 {path}: {exc}") from exc
    try:
        value = json.loads(raw.decode("utf-8")) if raw else {}
    except Exception as exc:
        raise ClientError(f"工作台返回的不是 JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ClientError(f"工作台 JSON 结构异常: {path}")
    return value


def stream_download(base: str, path: str, destination: Path) -> str:
    request = Request(base + path)
    try:
        with urlopen(request, timeout=120) as response, destination.open("xb") as target:
            if response.status != 200:
                raise ClientError(f"下载 {path} 失败（HTTP {response.status}）")
            while chunk := response.read(CHUNK_SIZE):
                target.write(chunk)
    except FileExistsError:
        raise
    except Exception as exc:
        if destination.exists():
            destination.unlink()
        raise ClientError(f"下载 {path} 失败: {exc}") from exc
    return digest(destination)


def reserve_output(folder: Path) -> None:
    if folder.exists():
        if not folder.is_dir():
            raise ClientError(f"输出路径不是目录: {folder}")
        if any(folder.iterdir()):
            raise ClientError(f"输出目录必须不存在或为空，拒绝覆盖已有内容: {folder}")
    else:
        folder.mkdir(parents=True)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def write_report(base: str, job: str, output: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    report = output / "report.json"
    temporary = output / "report.download.tmp"
    try:
        stream_download(base, f"/api/jobs/{job}/export/report", temporary)
        temporary.replace(report)
        return json.loads(report.read_text(encoding="utf-8"))
    except Exception:
        if temporary.exists():
            temporary.unlink()
        atomic_json(report, fallback)
        return fallback


def download_verified(base: str, job: str, state: dict[str, Any], output: Path) -> str:
    expected = state.get("export", {}).get("sha256")
    if not isinstance(expected, str) or len(expected) != 64:
        raise ClientError("工作台给出的正式导出缺少 SHA-256")
    temporary = output / "verified.download.tmp"
    actual = stream_download(base, f"/api/jobs/{job}/export/apk", temporary)
    if actual.lower() != expected.lower():
        temporary.unlink(missing_ok=True)
        raise ClientError(f"正式 APK 哈希不一致：工作台 {expected}，下载 {actual}")
    temporary.replace(output / "verified.apk")
    return actual


def download_experimental(base: str, job: str, state: dict[str, Any], output: Path) -> str | None:
    candidate = state.get("native_candidate")
    if not isinstance(candidate, dict) or candidate.get("acceptance") not in {"not-tested", "failed"}:
        return None
    temporary = output / "experimental.download.tmp"
    actual = stream_download(base, f"/api/jobs/{job}/export/native-candidate", temporary)
    expected = candidate.get("sha256")
    if isinstance(expected, str) and len(expected) == 64 and actual.lower() != expected.lower():
        temporary.unlink(missing_ok=True)
        raise ClientError(f"试验候选 APK 哈希不一致：工作台 {expected}，下载 {actual}")
    temporary.replace(output / "experimental.apk")
    return actual


def main() -> int:
    parser = argparse.ArgumentParser(
        description="经本机工作台导入、修复、验证并导出 APK；不会直接操作真机或修复引擎。"
    )
    parser.add_argument("apk", type=Path, help="输入 APK")
    parser.add_argument("--android", type=int, choices=sorted(ANDROID_TO_API), required=True, help="目标 Android 14/15/16")
    parser.add_argument("--output", type=Path, required=True, help="新的空输出目录；成功时含 verified.apk 与 report.json")
    parser.add_argument("--tap", help="可选的功能验证控件文案；必须与 --expect 同时给出")
    parser.add_argument("--expect", help="可选的功能验证预期文本；必须与 --tap 同时给出")
    parser.add_argument("--keep-candidate", action="store_true", help="若工作台保留未验收试验候选，下载为 experimental.apk")
    parser.add_argument("--workbench", default="http://127.0.0.1:8767", help="本机工作台地址")
    parser.add_argument("--poll-seconds", type=float, default=1.4, help="状态轮询间隔，至少 0.2 秒")
    parser.add_argument("--timeout-seconds", type=float, default=900, help="等待任务终态的超时秒数")
    args = parser.parse_args()
    if bool(args.tap) != bool(args.expect):
        parser.error("--tap 与 --expect 必须同时提供")
    if args.poll_seconds < 0.2 or args.timeout_seconds <= 0:
        parser.error("轮询间隔至少 0.2 秒，超时必须大于零")
    if not args.apk.is_file():
        parser.error(f"输入 APK 不存在: {args.apk}")

    try:
        host, port, base = parse_local_base(args.workbench)
        reserve_output(args.output)
        source_hash = digest(args.apk)
        config = read_json(base, "/api/config")
        token = config.get("token")
        api = ANDROID_TO_API[args.android]
        if not isinstance(token, str) or not token:
            raise ClientError("工作台未提供本次会话令牌")
        if not config.get("targets", {}).get(str(api)):
            raise ClientError(f"工作台未配置 Android {args.android}（API {api}）目标系统")

        print(f"导入 {args.apk.name}（SHA-256 {source_hash}）到 Android {args.android}…", flush=True)
        initial = stream_import(host, port, api, token, args.apk)
        job = initial.get("id")
        if not isinstance(job, str):
            raise ClientError("导入响应缺少任务编号")
        body = {"task": {"tap": args.tap, "expect": args.expect}} if args.tap else None
        post_json(base, f"/api/jobs/{job}/action/repair", token, body)
        print(f"已启动任务 {job}；等待工作台终态…", flush=True)
        deadline = time.monotonic() + args.timeout_seconds
        state: dict[str, Any]
        while True:
            state = read_json(base, f"/api/jobs/{job}")
            status = state.get("status")
            if status in {"ready", "error"}:
                break
            if time.monotonic() >= deadline:
                report_state = {**state, "cli": {"source_sha256": source_hash, "reason": "timeout"}}
                write_report(base, job, args.output, report_state)
                print(f"任务超时；已保存 {args.output / 'report.json'}", file=sys.stderr)
                return EXIT_TIMEOUT
            time.sleep(args.poll_seconds)

        state["cli"] = {"source_sha256": source_hash, "android": args.android, "api": api}
        write_report(base, job, args.output, state)
        experimental_hash = download_experimental(base, job, state, args.output) if args.keep_candidate else None
        if state.get("status") == "error":
            print(f"任务失败；诊断已保存至 {args.output / 'report.json'}", file=sys.stderr)
            return EXIT_JOB_ERROR
        if not state.get("export"):
            suffix = "；已保留 experimental.apk" if experimental_hash else ""
            print(f"任务完成但未达到正式导出门槛；报告位于 {args.output / 'report.json'}{suffix}", file=sys.stderr)
            return EXIT_NOT_EXPORTED
        verified_hash = download_verified(base, job, state, args.output)
        print(f"正式修复包已验证并保存: {args.output / 'verified.apk'}（SHA-256 {verified_hash}）")
        print(f"报告: {args.output / 'report.json'}")
        return EXIT_VERIFIED
    except ClientError as exc:
        print(f"repair_apk: {exc}", file=sys.stderr)
        return EXIT_OUTPUT_CONFLICT if "输出" in str(exc) else EXIT_HTTP_ERROR
    except KeyboardInterrupt:
        print("repair_apk: 已中断；若任务已创建，可在工作台中查看其状态。", file=sys.stderr)
        return EXIT_TIMEOUT


if __name__ == "__main__":
    raise SystemExit(main())
