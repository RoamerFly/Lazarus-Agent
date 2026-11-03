"""Versioned artifacts and local feedback for a workbench repair job."""
import base64
import binascii
import hashlib
import os
from pathlib import Path
import re
import shutil
import uuid

from fastapi import HTTPException


def add_message(state, role, text, **metadata):
    state.setdefault("messages", []).append({"role": role, "text": text, **metadata})


def archive_version(folder, state):
    """Copy an accepted export before later attempts can replace candidate.apk."""
    export = state.get("export")
    if not export:
        return None
    versions = state.setdefault("versions", [])
    for version in versions:
        if version["sha256"] == export["sha256"]:
            return version
    directory = folder / "versions"
    directory.mkdir(exist_ok=True)
    number = len(versions) + 1
    temp = directory / (uuid.uuid4().hex + ".part")
    try:
        shutil.copyfile(folder / export["file"], temp)
        with temp.open("rb") as f:
            if hashlib.file_digest(f, "sha256").hexdigest() != export["sha256"]:
                raise ValueError("历史版本校验失败")
        os.replace(temp, directory / f"{number}.apk")
    finally:
        temp.unlink(missing_ok=True)
    version = {"number": number, "sha256": export["sha256"], "level": export["level"],
               "environment": export.get("verified_environment"), "phone": "待真机复测"}
    if state.get("attempt_feedback"):
        version["feedback_id"]=state["attempt_feedback"]
    versions.append(version)
    add_message(state, "assistant", f"V{number} 已通过本轮验证（{version['level']}）。请在手机上复测，或描述仍然存在的问题。", version=number)
    return version


def attachment(folder, value):
    if not value:
        return None
    if not isinstance(value, str) or len(value) > 4_194_400:
        raise HTTPException(400, "截图请使用 3 MiB 以内的 PNG 或 JPEG")
    match = re.fullmatch(r"data:image/(png|jpeg);base64,([a-zA-Z0-9+/=]+)", value)
    if not match:
        raise HTTPException(400, "截图格式不支持")
    try:
        data = base64.b64decode(match[2], validate=True)
    except binascii.Error:
        raise HTTPException(400, "截图编码无效")
    header = b"\x89PNG\r\n\x1a\n" if match[1] == "png" else b"\xff\xd8\xff"
    if not data.startswith(header) or len(data) > 3 * 1024**2:
        raise HTTPException(400, "截图文件无效")
    name = uuid.uuid4().hex + (".png" if match[1]=="png" else ".jpg")
    directory = folder / "attachments"
    directory.mkdir(exist_ok=True)
    (directory / name).write_bytes(data)
    return name


def record_feedback(folder, state, body):
    text = body.get("text", "")
    kind = body.get("kind", "issue")
    if kind not in ("issue", "works") or not isinstance(text, str) or not 0 < len(text.strip()) <= 2000:
        raise HTTPException(400, "请填写 1 至 2000 字的问题或复测结果")
    versions = state.get("versions", [])
    number = body.get("version")
    version = next((v for v in versions if v["number"] == number), None)
    if number is not None and not version:
        raise HTTPException(400, "找不到反馈对应的版本")
    if kind == "works" and not version:
        raise HTTPException(400, "请选择已经下载复测的版本")
    task = body.get("task")
    if task is not None and (not isinstance(task,dict) or not all(
            isinstance(task.get(k),str) and 0<len(task[k].strip())<=120 for k in ("tap","expect"))):
        raise HTTPException(400, "请同时填写点击控件和预期内容")
    if not task:
        m = re.search(r'点击[「“"](.+?)[」”"].*?(?:应显示|应出现|预期(?:显示)?)[「“"](.+?)[」”"]', text)
        if m and max(map(len,m.groups()))<=120:
            task={"tap":m[1],"expect":m[2]}
    image = attachment(folder, body.get("attachment"))
    feedback_id = uuid.uuid4().hex
    add_message(state,"user",text.strip(),version=number,kind=kind,attachment=image,id=feedback_id)
    if kind=="works":
        version["phone"]="用户确认可运行"
        pending=state.get("pending_feedback")
        if pending and (pending["version"]==number or version.get("feedback_id")==pending["id"]):
            state.pop("pending_feedback",None)
        answer=f"已记录你对 V{number} 的真机确认。后续遇到问题，可继续在这里反馈。"
    else:
        if version:
            version["phone"]="用户反馈有问题"
        state["pending_feedback"]={"id":feedback_id,"version":number,"task":task,"text":text.strip()}
        answer=("已记录复现步骤。点击「继续修复」后，我会重新运行原包、检查故障并复测。"
                if task else "已记录问题。请补充点击的控件和预期内容，或使用「继续修复」重新执行现有诊断。截图会随本次反馈保存在本机。")
    add_message(state,"assistant",answer,version=number)
