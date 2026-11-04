"""User-defined APK changes, bound to an immutable input and observable checks."""
import re
import uuid

from compatibility_matrix import normalize_targets
from reviver import INSTALL_FLOOR


def short_text(value, label, limit=120):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ValueError(f"{label}需要 1 至 {limit} 个字符")
    return value.strip()


def remember_checks(criteria):
    selects = criteria.get("selects")
    if not isinstance(selects, list) or len(selects) != 2:
        raise ValueError("请填写两个不同文件的点击控件和正文，按 B、A 的顺序复测")
    rows = []
    for row in selects:
        if not isinstance(row, dict):
            raise ValueError("文件验收步骤格式无效")
        rows.append({key: short_text(row.get(key), label) for key, label in
                     (("tap", "点击控件"), ("expect", "文件打开后的正文"))})
    if rows[0]["tap"] == rows[1]["tap"] or rows[0]["expect"] == rows[1]["expect"]:
        raise ValueError("两份文件的控件和正文必须能分别辨认")
    return {"selects": rows}


# Every supported edit declares how it is requested and how the result is tested.
CAPABILITIES = {
    "remember-file": {
        "description": "记住上次打开的文件，强制停止后重开并核对两份文件的正文",
        "match": r"(记住|记忆|恢复|继续|重新打开|上次|最后).*?(文件|文档|记录)|remember.*file",
        "checks": remember_checks,
    },
}


def make_plan(state, body):
    request = short_text(body.get("request"), "改造需求", 1600)
    kind = body.get("kind", "auto")
    if kind == "auto":
        matches = [name for name, spec in CAPABILITIES.items() if re.search(spec["match"], request, re.I)]
        if len(matches) != 1 or re.search(r"不要|取消记忆|不再|关闭记忆", request):
            raise ValueError("请说明要实现的行为，或选择一种已支持的改造。当前无法自动确定这项需求。")
        kind = matches[0]
    if not isinstance(kind, str) or kind not in CAPABILITIES:
        raise ValueError("尚无对应的改造工具")
    criteria = body.get("criteria")
    if not isinstance(criteria, dict):
        raise ValueError("请先填写可复现的验收步骤")
    base = body.get("base_version")
    if base is None:
        digest = state["package"]["sha256"]
    else:
        if type(base) is not int:
            raise ValueError("基准版本无效")
        version = next((v for v in state.get("versions", []) if v["number"] == base), None)
        if not version:
            raise ValueError("找不到选定的基准版本")
        digest = version["sha256"]
    targets = normalize_targets(body.get("targets"), INSTALL_FLOOR, state["api"])
    if max(targets) != state["api"]:
        raise ValueError("请先将当前任务切换到最高目标 Android 版本")
    spec = CAPABILITIES[kind]
    return {"id": uuid.uuid4().hex, "kind": kind, "request": request,
            "description": spec["description"], "base_version": base, "source_sha256": digest,
            "targets": targets, "criteria": spec["checks"](criteria)}
