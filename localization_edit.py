"""Bounded Android string-resource localization transform.

Simple default strings and plain items in existing Chinese plural resources are eligible. The public API uses
their displayed text, while this module writes the Android/XML spelling needed
to retain special characters and formatter placeholders. It never changes
dex, smali, plurals, arrays, aliases or non-translatable resources.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from pathlib import Path
import re
import xml.etree.ElementTree as ET


PLAN_KIND = "add-zh-cn-resources-v1"
_RESOURCE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*\Z")
_FORMAT = re.compile(r"%(?:(\d+)\$)?[-#+ 0,(<]*\d*(?:\.\d+)?(?:[tT][a-zA-Z]|[a-zA-Z])")
_MAX_CATALOG = 1500


class LocalizationError(ValueError):
    """A supported resource-only localization cannot be proven safely."""


def _decode_android_escapes(value: str) -> str:
    """Decode only an unambiguous subset of apktool's Android escapes."""
    out, index = [], 0
    escapes = {"\\": "\\", "'": "'", '"': '"', "@": "@", "?": "?", "%": "%",
               "n": "\n", "r": "\r", "t": "\t"}
    while index < len(value):
        char = value[index]
        if char != "\\":
            out.append(char)
            index += 1
            continue
        if index + 1 >= len(value):
            raise LocalizationError("字符串包含未闭合反斜杠转义")
        next_char = value[index + 1]
        if next_char == "u":
            part = value[index + 2:index + 6]
            if len(part) != 4 or not re.fullmatch(r"[0-9A-Fa-f]{4}", part):
                raise LocalizationError("字符串包含不安全的 Unicode 转义")
            out.append(chr(int(part, 16)))
            index += 6
            continue
        if next_char not in escapes:
            raise LocalizationError("字符串包含无法安全解释的反斜杠转义")
        out.append(escapes[next_char])
        index += 2
    return "".join(out)


def _format_info(value: str, formatted: bool) -> dict:
    """Parse Android's Java formatter subset from user-visible text.

    Positional arguments may move in a translation. Unnumbered arguments consume
    values in order and therefore must remain ordered. Literal percent signs are
    normal user text and are escaped at output time.
    """
    if not formatted:
        return {"indexed": (), "ordered": (), "newline": 0, "percent": 0}
    indexed, ordered, index = [], [], 0
    newline = percent = 0
    while index < len(value):
        if value[index] != "%":
            index += 1
            continue
        if index + 1 < len(value) and value[index + 1] == "%":
            percent += 2
            index += 2
            continue
        if index + 1 < len(value) and value[index + 1] == "n":
            newline += 1
            index += 2
            continue
        match = _FORMAT.match(value, index)
        if not match:
            percent += 1
            index += 1
            continue
        token = match.group(0)
        position = match.group(1)
        if position:
            indexed.append((int(position), token[token.index("$") + 1:]))
        else:
            ordered.append(token)
        index = match.end()
    if indexed and ordered:
        raise LocalizationError("不能混用有序和无序格式化占位符")
    return {"indexed": tuple(sorted(indexed)), "ordered": tuple(ordered),
            "newline": newline, "percent": percent}


def _assert_format_compatible(original: str, translated: str, formatted: bool) -> None:
    source, target = _format_info(original, formatted), _format_info(translated, formatted)
    if source != target:
        raise LocalizationError(f"格式化占位符或百分号不一致：{original}")


def _display_value(raw: str, formatted: bool) -> str:
    value = _decode_android_escapes(raw)
    if not formatted:
        return value
    return value.replace("%%", "%")


def _is_alias(raw: str) -> bool:
    return raw.lstrip().startswith(("@", "?"))


_QUANTITIES = {"zero", "one", "two", "few", "many", "other"}


def valid_resource_ref(name):
    if not isinstance(name, str):
        return False
    if _RESOURCE_NAME.fullmatch(name):
        return True
    match = re.fullmatch(r"plurals/([A-Za-z_][A-Za-z0-9_.]*)\[([a-z]+)\]", name)
    return bool(match and match.group(2) in _QUANTITIES)


def _text_nodes(root):
    for element in root.findall("string"):
        name = element.get("name", "")
        if _RESOURCE_NAME.fullmatch(name):
            yield name, element, element
    for group in root.findall("plurals"):
        name = group.get("name", "")
        if not _RESOURCE_NAME.fullmatch(name):
            continue
        for element in group.findall("item"):
            quantity = element.get("quantity")
            if quantity in _QUANTITIES:
                yield f"plurals/{name}[{quantity}]", element, group


def _simple_strings(path: Path):
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise LocalizationError(f"字符串资源 XML 无法解析：{path.as_posix()}") from exc
    if root.tag != "resources":
        return []
    rows = []
    for name, element, group in _text_nodes(root):
        if list(element):
            continue
        if element.text is None or element.tail and element.tail.strip():
            continue
        raw = element.text
        if _is_alias(raw):
            continue
        formatted = group.get("formatted") != "false"
        try:
            value = _display_value(raw, formatted)
            _format_info(value, formatted)
        except LocalizationError:
            # It is safer to leave an ambiguous string out of the planner.
            continue
        rows.append({"name": name, "original": value, "translatable": group.get("translatable") != "false",
                     "formatted": formatted, "source_file": path})
    return rows


def _default_resources(decoded: Path):
    values = decoded / "res" / "values"
    if not values.is_dir():
        return []
    rows = []
    for path in sorted(values.glob("*.xml")):
        for row in _simple_strings(path):
            row["source_file"] = row["source_file"].relative_to(decoded).as_posix()
            rows.append(row)
    return rows


def _is_zh_cn_directory(name: str) -> bool:
    """Do not conflate Traditional Chinese or generic Chinese with zh-CN."""
    if not name.startswith("values-"):
        return False
    qualifiers = name.split("-")[1:]
    if "zh" in qualifiers and "rCN" in qualifiers:
        return True
    for qualifier in qualifiers:
        if qualifier.startswith("b+"):
            parts = qualifier[2:].split("+")
            if "zh" in parts and ("CN" in parts or "Hans" in parts):
                return True
    return False


def _zh_cn_existing(decoded: Path):
    """Index only existing zh-CN values, retaining each concrete XML path."""
    answer: dict[str, list[dict]] = defaultdict(list)
    res = decoded / "res"
    if not res.is_dir():
        return answer
    for folder in sorted(res.iterdir()):
        if not folder.is_dir() or not _is_zh_cn_directory(folder.name):
            continue
        for path in sorted(folder.glob("*.xml")):
            for row in _simple_strings(path):
                row["source_file"] = row["source_file"].relative_to(decoded).as_posix()
                answer[row["name"]].append(row)
    return answer


def _zh_cn_occupied_names(decoded: Path):
    """Find every declared zh-CN string name, including aliases we cannot edit."""
    answer: dict[str, list[str]] = defaultdict(list)
    res = decoded / "res"
    if not res.is_dir():
        return answer
    for folder in sorted(res.iterdir()):
        if not folder.is_dir() or not _is_zh_cn_directory(folder.name):
            continue
        for path in sorted(folder.glob("*.xml")):
            try:
                root = ET.parse(path).getroot()
            except ET.ParseError as exc:
                raise LocalizationError(f"字符串资源 XML 无法解析：{path.as_posix()}") from exc
            if root.tag != "resources":
                continue
            for name, _, _group in _text_nodes(root):
                answer[name].append(path.relative_to(decoded).as_posix())
    return answer


def catalog(decoded: Path) -> list[dict]:
    """List simple, translatable default strings and known zh-CN values only."""
    decoded = Path(decoded)
    existing = _zh_cn_existing(decoded)
    rows = []
    for row in _default_resources(decoded):
        if not row["translatable"]:
            continue
        values = existing.get(row["name"], [])
        if row["name"].startswith("plurals/") and len(values) != 1:
            continue
        translated = values[0]["original"] if len(values) == 1 else ""
        rows.append({"name": row["name"], "original": row["original"], "translated": translated})
        if len(rows) >= _MAX_CATALOG:
            break
    return rows


def _validate_criteria(criteria):
    if not isinstance(criteria, dict):
        raise LocalizationError("汉化条件必须是对象")
    translations = criteria.get("translations")
    if not isinstance(translations, dict) or not 1 <= len(translations) <= 100:
        raise LocalizationError("需要 1 至 100 条原文到译文映射")
    clean = {}
    for original, translated in translations.items():
        if not isinstance(original, str) or not isinstance(translated, str) or not original or not translated:
            raise LocalizationError("原文和译文必须为非空字符串")
        if len(original) > 500 or len(translated) > 500 or "\x00" in original + translated:
            raise LocalizationError("原文或译文长度、字符不安全")
        if original == translated:
            raise LocalizationError("原文和译文不能相同")
        clean[original] = translated
    resources = criteria.get("resources", {})
    if not isinstance(resources, dict) or not resources.keys() <= clean.keys():
        raise LocalizationError("资源名必须对应本次原文")
    choices = {}
    for original, name in resources.items():
        if name is not None and not valid_resource_ref(name):
            raise LocalizationError("资源名称无效")
        choices[original] = name
    checks = criteria.get("checks")
    if not isinstance(checks, list) or not 1 <= len(checks) <= len(clean):
        raise LocalizationError("至少需要一条界面验收译文")
    checked = set()
    normalized_checks = []
    for check in checks:
        if not isinstance(check, dict) or set(check) != {"original", "translated"}:
            raise LocalizationError("译文验收项格式无效")
        original, translated = check["original"], check["translated"]
        if original not in clean or clean[original] != translated or original in checked:
            raise LocalizationError("译文验收项必须和本次映射逐项一致")
        checked.add(original)
        normalized_checks.append({"original": original, "translated": translated})
    return {"translations": clean, "resources": choices, "checks": normalized_checks}


def inspect(decoded: Path, criteria: dict) -> dict:
    decoded = Path(decoded)
    if not decoded.is_dir():
        raise LocalizationError("解包目录不存在")
    requested = _validate_criteria(criteria)
    indexed: dict[str, list[dict]] = defaultdict(list)
    for row in _default_resources(decoded):
        indexed[row["original"]].append(row)
    existing = _zh_cn_existing(decoded)
    occupied = _zh_cn_occupied_names(decoded)
    edits = []
    for original, translated in requested["translations"].items():
        matches = indexed.get(original, [])
        selected = requested["resources"].get(original)
        if selected is not None:
            matches = [row for row in matches if row["name"] == selected]
        if len(matches) != 1:
            raise LocalizationError(f"无法唯一定位原文资源：{original}；请用 resources 指定资源名")
        match = matches[0]
        if not match["translatable"]:
            raise LocalizationError(f"资源标记为不可翻译：{match['name']}")
        _assert_format_compatible(original, translated, match["formatted"])
        current = existing.get(match["name"], [])
        if match["name"].startswith("plurals/") and (len(current) != 1 or not current[0]["translatable"]):
            raise LocalizationError("仅支持补译已有中文数量规则，无法唯一定位目标条目")
        declarations = occupied.get(match["name"], [])
        if len(current) > 1 or len(declarations) > 1:
            raise LocalizationError(f"存在重复的 zh-CN 资源，拒绝猜测写入位置：{match['name']}")
        if not current and declarations:
            raise LocalizationError(f"现有 zh-CN 资源不是可安全编辑的普通字符串：{match['name']}")
        target_file = current[0]["source_file"] if current else "res/values-zh-rCN/strings.xml"
        edits.append({"name": match["name"], "original": original, "translated": translated,
                      "formatted": match["formatted"], "source_file": match["source_file"],
                      "target_file": target_file})
    plan = {"kind": PLAN_KIND, "criteria": requested, "target_locale": "zh-CN", "edits": edits}
    return {"available": True, "plan": plan,
            "description": "仅为已定位的简单字符串资源新增或更新简体中文覆盖，不改动 dex 或 smali",
            "scope": "普通字符串及已有中文数量条目。保留格式化占位符，拒绝别名、重复和不安全转义"}


def _target(decoded: Path, relative: str) -> Path:
    target = decoded / Path(relative)
    root = decoded.resolve()
    if root not in target.resolve().parents:
        raise LocalizationError("目标资源路径无效")
    return target


def _android_text(value: str, formatted: bool) -> str:
    """Encode user-visible text as an Android string-resource literal."""
    _format_info(value, formatted)
    out, index, seen_nonspace = [], 0, False
    while index < len(value):
        char = value[index]
        if formatted and char == "%":
            if index + 1 < len(value) and value[index + 1] == "n":
                out.append("%n")
                index += 2
                continue
            match = _FORMAT.match(value, index)
            if match:
                out.append(match.group(0))
                index = match.end()
                continue
            out.append("%%")
            index += 1
            continue
        if char == "\\":
            out.append("\\\\")
        elif char == "'":
            out.append("\\'")
        elif char == '"':
            out.append('\\"')
        elif not seen_nonspace and char in "@?":
            out.append("\\" + char)
        else:
            out.append(char)
        if not char.isspace():
            seen_nonspace = True
        index += 1
    return "".join(out)


def _write_zh_file(path: Path, edits: list[dict]):
    if path.exists():
        try:
            tree = ET.parse(path)
        except ET.ParseError as exc:
            raise LocalizationError("现有中文资源 XML 无法解析") from exc
        root = tree.getroot()
        if root.tag != "resources":
            raise LocalizationError("中文资源根节点无效")
    else:
        root = ET.Element("resources")
        tree = ET.ElementTree(root)
    by_name = {element.get("name"): element for element in root.findall("string") if element.get("name")}
    quantities = {key: element for key, element, group in _text_nodes(root) if group is not element}
    for edit in edits:
        if edit["name"].startswith("plurals/"):
            if edit["name"] not in quantities:
                raise LocalizationError("中文数量资源条目已变更")
            quantities[edit["name"]].text = _android_text(edit["translated"], edit["formatted"])
            continue
        element = by_name.get(edit["name"])
        if element is None:
            attributes = {"name": edit["name"]}
            if not edit["formatted"]:
                attributes["formatted"] = "false"
            element = ET.SubElement(root, "string", attributes)
            by_name[edit["name"]] = element
        elif list(element) or element.get("translatable") == "false" or _is_alias(element.text or ""):
            raise LocalizationError(f"现有中文资源不可安全覆盖：{edit['name']}")
        if edit["formatted"]:
            element.attrib.pop("formatted", None)
        else:
            element.set("formatted", "false")
        element.text = _android_text(edit["translated"], edit["formatted"])
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(tree, space="    ")
    tree.write(path, encoding="utf-8", xml_declaration=True)


def apply(decoded: Path, plan: dict) -> dict:
    if not isinstance(plan, dict) or plan.get("kind") != PLAN_KIND:
        raise LocalizationError("不是可执行的简体中文资源计划")
    found = inspect(decoded, plan.get("criteria"))
    if plan != found["plan"]:
        raise LocalizationError("计划与当前解包 APK 的可验证资源不一致")
    grouped: dict[str, list[dict]] = defaultdict(list)
    for edit in plan["edits"]:
        grouped[edit["target_file"]].append(deepcopy(edit))
    changed = []
    for relative, edits in grouped.items():
        path = _target(Path(decoded), relative)
        _write_zh_file(path, edits)
        changed.append(relative)
    return {"applied": True, "kind": PLAN_KIND, "rule": PLAN_KIND, "changed_files": changed,
            "edits": len(plan["edits"]), "checks": plan["criteria"]["checks"],
            "audit": {"kind": PLAN_KIND, "target_locale": "zh-CN", "resource_only": True}}
