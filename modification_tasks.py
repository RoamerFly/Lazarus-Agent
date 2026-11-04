"""User-defined APK changes, bound to an immutable input and observable checks."""
import json
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


def localization_checks(criteria):
    translations = criteria.get("translations")
    if not isinstance(translations, dict) or not 1 <= len(translations) <= 100:
        raise ValueError("请填写 1 至 100 条译文")
    entries = {}
    for original, translated in translations.items():
        original = short_text(original, "原文", 500)
        translated = short_text(translated, "译文", 500)
        if original == translated or original in entries:
            raise ValueError("原文必须唯一且与译文不同")
        entries[original] = translated
    resources = criteria.get("resources", {})
    if not isinstance(resources, dict) or not resources.keys() <= entries.keys():
        raise ValueError("资源名称必须对应本次翻译的原文")
    if any(not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", name)
           for name in resources.values()):
        raise ValueError("字符串资源名称无效")
    checks = criteria.get("checks")
    if not isinstance(checks, list) or not 1 <= len(checks) <= len(entries):
        raise ValueError("请标记至少一条启动界面中可见的译文用于验收")
    visible = {}
    for row in checks:
        if not isinstance(row, dict):
            raise ValueError("译文验收项格式无效")
        original, translated = row.get("original"), row.get("translated")
        if not isinstance(original, str) or original not in entries or entries[original] != translated or original in visible:
            raise ValueError("验收项必须与本次翻译逐项对应")
        visible[original] = translated
    verified = {"translations": entries, "resources": dict(resources),
                "checks": [{"original": key, "translated": val} for key, val in visible.items()]}
    operation = criteria.get("operation")
    if operation is not None:
        if not isinstance(operation, dict):
            raise ValueError("验收操作格式无效")
        verified["operation"] = {key: short_text(operation.get(key), label, 500)
                                 for key, label in (("tap", "操作控件"), ("expect", "操作后的界面文字"))}
    return verified


# Every supported edit declares how it is requested and how the result is tested.
CAPABILITIES = {
    "localize-zh": {
        "description": "为选定字符串增加简体中文，验证中英文切换与回退",
        "match": r"汉化|简体中文|翻译.{0,12}中文|中文.{0,8}(翻译|语言|界面)|locali[sz]e",
        "checks": localization_checks,
    },
    "remember-file": {
        "description": "记住上次打开的文件，强制停止后重开并核对两份文件的正文",
        "match": r"(记住|记忆|恢复|继续|重新打开|上次|最后).*?(文件|文档|记录)|remember.*file",
        "checks": remember_checks,
    },
}


def choose_capability(request, kind, use_model):
    if type(use_model) is not bool:
        raise ValueError("模型选项必须为布尔值")
    if kind != "auto":
        return kind, "manual"
    source = "rules"
    if use_model:
        from model_settings import get_config
        from model_tools import ModelToolError, _request
        config = get_config()
        if config["url"] and config["model"]:
            catalog = {key: spec["description"] for key,spec in CAPABILITIES.items()}
            name = "choose_modification"
            tools = [{"type":"function","function":{"name":name,"description":"选择与需求完全匹配的工具",
                      "parameters":{"type":"object","properties":{"kind":{"type":"string",
                      "enum":[*catalog,"unsupported"]}},"required":["kind"],"additionalProperties":False}}}]
            try:
                reply = _request(config["url"], {"model":config["model"],"tools":tools,
                    "tool_choice":{"type":"function","function":{"name":name}},
                    "messages":[{"role":"system","content":"阅读给出的用户需求，从目录中选一个能完整满足的工具。"
                                "遇到不匹配、否定或需澄清的需求选择 unsupported。仅返回工具调用。"},
                                {"role":"user","content":json.dumps({"request":request,"catalog":catalog},ensure_ascii=False)}]},
                                config["key"])
                calls = reply["tool_calls"]
                if len(calls)!=1 or calls[0]["function"]["name"]!=name:
                    raise ValueError("不符合工具协议")
                args = json.loads(calls[0]["function"]["arguments"])
                if set(args)!={"kind"} or args["kind"] not in (*catalog,"unsupported"):
                    raise ValueError("未知工具")
                return args["kind"], "model"
            except (ModelToolError,ValueError,KeyError,TypeError,IndexError):
                source = "fallback"
        else:
            source = "fallback"
    matches = [key for key,spec in CAPABILITIES.items() if re.search(spec["match"],request,re.I)]
    if len(matches)!=1 or re.search(r"不要|取消记忆|不再|关闭记忆", request):
        raise ValueError("请说明要实现的行为，或选择一种已支持的改造。当前无法自动确定这项需求。")
    return matches[0], source


def make_plan(state, body):
    request = short_text(body.get("request"), "改造需求", 1600)
    kind, source = choose_capability(request, body.get("kind", "auto"), body.get("use_model",False))
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
            "targets": targets, "tool_source":source, "criteria": spec["checks"](criteria)}
