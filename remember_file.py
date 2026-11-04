"""Bounded smali transformation for remembering a selected local record.

This is intentionally narrower than a general code rewriter.  It only accepts
an Activity whose ``onCreate`` opens one literal default record through a
same-class ``(String) -> void`` method.  Persisting the argument at the entry
of that method means every other verified caller, such as a record-picker
button, updates the stored selection too.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


MARKER = "Lazarus Agent remember-last-record v1"
PLAN_KIND = "remember-last-record-v1"
_CLASS = re.compile(r"^\.class\b.*?\s(L[^;\s]+;)$", re.MULTILINE)
_SUPER = re.compile(r"^\.super\s+(L[^;\s]+;)$", re.MULTILINE)
_METHOD = re.compile(
    r"(?ms)^\.method\s+(?P<header>[^\n]+)\n(?P<body>.*?)^\.end method\s*$"
)
_METHOD_SIGNATURE = re.compile(r"(?P<name>[A-Za-z_$][\w$]*)\((?P<args>[^)]*)\)(?P<result>\S+)$")
_OPEN_CALL = re.compile(
    r"^\s*invoke-(?:direct|virtual)\s+\{\s*p0\s*,\s*(?P<register>[vp]\d+)\s*\},\s*"
    r"(?P<owner>L[^;]+;)->(?P<name>[A-Za-z_$][\w$]*)\(Ljava/lang/String;\)V\s*$"
)
_CONST_STRING = re.compile(r'^\s*const-string(?:/jumbo)?\s+(?P<register>[vp]\d+)\s*,\s*"(?P<value>(?:[^"\\]|\\.)*)"\s*$')
_REGISTER_DIRECTIVE = re.compile(r"^\s*\.(?:locals|registers)\s+\d+\s*$", re.MULTILINE)
_ACTUAL_READ = re.compile(
    r"^\s*invoke-(?P<kind>[\w/-]+)\s+\{(?P<arguments>[^}]*)\},\s*"
    r"(?P<owner>L(?:android/content/res/AssetManager|android/content/Context|java/io/FileInputStream);)"
    r"->(?P<name>open|openFileInput|<init>)\(Ljava/lang/String;[^)]*\)(?P<result>\S+)\s*$"
)
_MOVE_RESULT_OBJECT = re.compile(r"^\s*move-result-object\s+[vp]\d+\s*$")
# ``p1`` is the String parameter of the verified target method.  A number of
# compilers reuse parameter registers as scratch space; after such a write it
# is no longer sound to persist it.  Keep this conservative: ``check-cast``
# is deliberately not included because it preserves the same object.
_P1_WRITE = re.compile(
    r"^\s*(?:move(?:-object(?:/from16|/16)?|(?:/from16|/16)?)?|"
    r"move-result(?:-object(?:/16)?|(?:-wide(?:/16)?)?)?|move-exception|"
    r"const[\w/-]*|new-instance|new-array|filled-new-array(?:/range)?|"
    r"(?:[a-z]+get(?:-[\w]+)?|aget(?:-[\w]+)?|[a-z]+(?:to|add|sub|mul|div|rem|and|or|xor|shl|shr|ushr)[\w/-]*)"
    r")\s+p1(?:\s*,|\s*$)"
)
_HELPERS = ("lazarusSaveLastRecord", "lazarusLoadLastRecord")


class RememberFileError(ValueError):
    """A safe refusal that can be shown as an unsupported modification plan."""


@dataclass(frozen=True)
class Candidate:
    relative_path: str
    descriptor: str
    target_name: str
    default_record: str
    source_register: str
    selection_records: tuple[str, ...]
    selection_call_count: int

    def plan(self) -> dict:
        return {
            "kind": PLAN_KIND,
            "class_path": self.relative_path,
            "class_descriptor": self.descriptor,
            "target_method": self.target_name,
            "default_record": self.default_record,
            "source_register": self.source_register,
            "selection_records": list(self.selection_records),
            "selection_call_count": self.selection_call_count,
        }


def _unescape_smali(value: str) -> str:
    """Decode common escapes without corrupting non-ASCII literal text."""
    escapes = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\"}
    result: list[str] = []
    offset = 0
    while offset < len(value):
        char = value[offset]
        if char != "\\":
            result.append(char)
            offset += 1
            continue
        if offset + 1 >= len(value) or value[offset + 1] not in escapes:
            raise RememberFileError("默认记录字符串包含不支持的转义")
        result.append(escapes[value[offset + 1]])
        offset += 2
    return "".join(result)


def _methods(text: str):
    for match in _METHOD.finditer(text):
        signature = _METHOD_SIGNATURE.search(match.group("header").strip())
        if signature:
            yield match, signature


def _existing_target(text: str, target_name: str) -> bool:
    for match, signature in _methods(text):
        if signature.group("name") != target_name:
            continue
        unsafe = {" static ", " abstract ", " native "}
        header = " " + match.group("header") + " "
        if (signature.group("args") == "Ljava/lang/String;" and signature.group("result") == "V"
                and not any(flag in header for flag in unsafe)
                and _REGISTER_DIRECTIVE.search(match.group("body"))):
            return True
    return False


def _has_helper_collision(text: str) -> bool:
    return any(signature.group("name") in _HELPERS for _method, signature in _methods(text))


def _physical_register(method: re.Match[str], register: str, parameter_count: int) -> int | None:
    """Return an instruction's physical register or refuse a non-encodable call."""
    directive = _REGISTER_DIRECTIVE.search(method.group("body"))
    if not directive:
        return None
    count = int(directive.group(0).split()[-1])
    if directive.group(0).lstrip().startswith(".locals"):
        base = count
    else:
        base = count - parameter_count
    if base < 0:
        return None
    if register.startswith("p"):
        return base + int(register[1:])
    return int(register[1:])


def _record_literal_before(lines: list[str], index: int, register: str) -> str | None:
    """Only accept a literal directly adjacent to the open call.

    Skipping labels or directives can cross a branch boundary.  A blank/comment
    line is harmless but any instruction, label, or directive breaks the proof.
    """
    offset = index - 1
    while offset >= 0 and (not lines[offset].strip() or lines[offset].lstrip().startswith("#")):
        offset -= 1
    literal = _CONST_STRING.match(lines[offset] if offset >= 0 else "")
    if not literal or literal.group("register") != register:
        return None
    return _unescape_smali(literal.group("value"))


def _record_literal_in_block(lines: list[str], index: int, register: str) -> str | None:
    """Trace a literal register definition within one straight-line block.

    D8 often leaves several harmless UI instructions between a const-string
    and the final call.  We can follow it only until a label, control-flow
    instruction, or another write to that register; that never crosses a
    branch merge or guesses a value from a different basic block.
    """
    for offset in range(index - 1, -1, -1):
        line = lines[offset].strip()
        if not line or line.startswith("#") or line.startswith(".line"):
            continue
        if line.startswith(":") or line.startswith(".") or re.match(r"(?:if-|goto|packed-switch|sparse-switch)\b", line):
            return None
        literal = _CONST_STRING.match(lines[offset])
        if literal and literal.group("register") == register:
            return _unescape_smali(literal.group("value"))
        # Most smali result-producing instructions put their destination first.
        # Treat any such overwrite as a hard data-flow boundary.  Invokes use
        # a register set in braces and therefore do not match this pattern.
        if re.match(r"\S+\s+" + re.escape(register) + r"(?:\s*,|\s*$)", line):
            return None
    return None


def _read_location(method: re.Match[str]) -> tuple[int, int] | None:
    """Return a proven stream-open call and its successful-result position."""
    lines = method.group("body").splitlines()
    found: list[tuple[int, int]] = []
    for index, line in enumerate(lines):
        access = _ACTUAL_READ.match(line)
        if not access:
            continue
        arguments = [item.strip() for item in access.group("arguments").split(",")]
        if "p1" not in arguments:
            continue
        # FileInputStream construction has no result register.  Completing the
        # constructor is the first point at which its stream exists.  Every
        # open/openFileInput call must immediately produce an object result.
        if access.group("result") == "V":
            found.append((index, index))
        else:
            # Apktool commonly emits an empty source-line separator between an
            # invoke and move-result-object.  Permit only blank/comment lines;
            # labels/directives/instructions would break the proven pair.
            result_index = index + 1
            while result_index < len(lines) and (not lines[result_index].strip()
                                                 or lines[result_index].lstrip().startswith("#")):
                result_index += 1
            if result_index < len(lines) and _MOVE_RESULT_OBJECT.match(lines[result_index]):
                found.append((index, result_index))
    return found[0] if len(found) == 1 else None


def _read_insertion_index(method: re.Match[str]) -> int | None:
    location = _read_location(method)
    return location[1] if location else None


def _safe_capture_register(method: re.Match[str]) -> str | None:
    """Reserve one new low local and retain encodable direct invokes.

    We deliberately call the helper as ``{p0, vSaved}``, rather than placing
    the saved String back in p1: p1 can be the live InputStream result needed
    by subsequent instructions.
    """
    directive = _REGISTER_DIRECTIVE.search(method.group("body"))
    if not directive:
        return None
    count = int(directive.group(0).split()[-1])
    if directive.group(0).lstrip().startswith(".locals"):
        saved, receiver_after = count, count + 1
    else:
        # With `.registers`, a hand-written method can address parameter
        # physical slots as vN instead of pN.  Growing the register file would
        # then silently change their meaning, so reject rather than rewrite.
        parameter_base = count - 2
        if any(int(value) >= parameter_base for value in re.findall(r"\bv(\d+)\b", method.group("body"))):
            return None
        saved, receiver_after = count - 2, count - 1
    if saved < 0 or saved > 15 or receiver_after > 15:
        return None
    return f"v{saved}"


def _selection_paths(text: str, descriptor: str, target_name: str, startup: re.Match[str]) -> tuple[tuple[str, ...], int]:
    """Find an alternative caller that supplies a String selected at runtime.

    A literal selector is useful evidence, but an Android button commonly
    passes ``View.getTag()`` after ``check-cast String``.  Either form is a
    concrete call path to the same verified file-opening method.  We never
    infer selection from arbitrary text shown in the UI.
    """
    values: set[str] = set()
    count = 0
    for method, _signature in _methods(text):
        if method.start() == startup.start():
            continue
        lines = method.group("body").splitlines()
        for index, line in enumerate(lines):
            call = _OPEN_CALL.match(line)
            if call and call.group("owner") == descriptor and call.group("name") == target_name:
                literal = _record_literal_before(lines, index, call.group("register"))
                if literal is not None:
                    values.add(literal)
                    count += 1
                    continue
                cast = re.compile(r"^\s*check-cast\s+" + re.escape(call.group("register")) + r",\s*Ljava/lang/String;\s*$")
                if any(cast.match(entry) for entry in lines[:index]):
                    count += 1
    return tuple(sorted(values)), count


def _candidate_for_file(root: Path, path: Path) -> list[Candidate]:
    text = path.read_text(encoding="utf-8")
    if MARKER in text or _has_helper_collision(text):
        return []
    descriptor = _CLASS.search(text)
    parent = _SUPER.search(text)
    # The helper deliberately invokes Activity.getSharedPreferences.  Do not
    # infer inheritance from a support library or an unknown intermediate base.
    if not descriptor or not parent or parent.group(1) != "Landroid/app/Activity;":
        return []
    descriptor_value = descriptor.group(1)
    candidates: list[Candidate] = []
    for _method, signature in _methods(text):
        if signature.group("name") != "onCreate" or signature.group("args") != "Landroid/os/Bundle;" or signature.group("result") != "V":
            continue
        body_lines = _method.group("body").splitlines()
        for index, line in enumerate(body_lines):
            call = _OPEN_CALL.match(line)
            if not call or call.group("owner") != descriptor_value:
                continue
            if not _existing_target(text, call.group("name")):
                continue
            default = _record_literal_in_block(body_lines, index, call.group("register"))
            target_method = next(method for method, target_signature in _methods(text)
                                 if target_signature.group("name") == call.group("name")
                                 and target_signature.group("args") == "Ljava/lang/String;"
                                 and target_signature.group("result") == "V")
            if default is None or _read_insertion_index(target_method) is None or _safe_capture_register(target_method) is None:
                continue
            if _physical_register(_method, "p0", 2) is None or _physical_register(_method, "p0", 2) > 15:
                continue
            if _physical_register(_method, call.group("register"), 2) is None or _physical_register(_method, call.group("register"), 2) > 15:
                continue
            selections, selection_count = _selection_paths(text, descriptor_value, call.group("name"), _method)
            candidates.append(Candidate(
                relative_path=path.relative_to(root).as_posix(),
                descriptor=descriptor_value,
                target_name=call.group("name"),
                default_record=default,
                source_register=call.group("register"),
                selection_records=selections,
                selection_call_count=selection_count,
            ))
    return candidates


def inspect(decoded: Path, criteria=None) -> dict:
    """Inspect an apktool decoded directory without modifying it.

    A single candidate is required.  Multiple literal opens might represent
    different startup modes, and choosing one would make the transformation
    ambiguous.
    """
    decoded = Path(decoded)
    if not decoded.is_dir():
        raise RememberFileError("解包目录不存在")
    paths = sorted(path for path in decoded.rglob("*.smali") if path.is_file())
    candidates = [candidate for path in paths for candidate in _candidate_for_file(decoded, path)]
    if not candidates:
        return {"available": False, "reason": "未找到可确认的 Activity 默认记录打开路径"}
    if len(candidates) != 1:
        return {"available": False, "reason": "发现多个默认记录打开路径，无法安全选择", "candidate_count": len(candidates)}
    candidate = candidates[0]
    if not candidate.selection_call_count:
        return {"available": False, "reason": "未找到切换到其他记录的调用路径"}
    if candidate.selection_records and not any(value != candidate.default_record for value in candidate.selection_records):
        return {"available": False, "reason": "切换调用没有提供不同于默认记录的值"}
    return {
        "available": True,
        "plan": candidate.plan(),
        "description": "保存同一记录打开方法的参数，并在冷启动时恢复最后一次选择",
        "scope": "仅支持直接继承 android.app.Activity、同类 String 打开方法、可确认流打开及另一条记录切换路径的简单 smali 结构",
    }


def _helpers() -> str:
    return f'''\n# {MARKER}\n.method private lazarusSaveLastRecord(Ljava/lang/String;)V\n    .locals 2\n\n    const-string v0, "lazarus.remember.last.record"\n    const/4 v1, 0x0\n    invoke-virtual {{p0, v0, v1}}, Landroid/app/Activity;->getSharedPreferences(Ljava/lang/String;I)Landroid/content/SharedPreferences;\n    move-result-object v0\n    invoke-interface {{v0}}, Landroid/content/SharedPreferences;->edit()Landroid/content/SharedPreferences$Editor;\n    move-result-object v0\n    const-string v1, "record"\n    invoke-interface {{v0, v1, p1}}, Landroid/content/SharedPreferences$Editor;->putString(Ljava/lang/String;Ljava/lang/String;)Landroid/content/SharedPreferences$Editor;\n    move-result-object v0\n    invoke-interface {{v0}}, Landroid/content/SharedPreferences$Editor;->apply()V\n    return-void\n.end method\n\n.method private lazarusLoadLastRecord(Ljava/lang/String;)Ljava/lang/String;\n    .locals 2\n\n    const-string v0, "lazarus.remember.last.record"\n    const/4 v1, 0x0\n    invoke-virtual {{p0, v0, v1}}, Landroid/app/Activity;->getSharedPreferences(Ljava/lang/String;I)Landroid/content/SharedPreferences;\n    move-result-object v0\n    const-string v1, "record"\n    invoke-interface {{v0, v1, p1}}, Landroid/content/SharedPreferences;->getString(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;\n    move-result-object v0\n    return-object v0\n.end method\n'''


def _insert_target_hook(text: str, target_name: str) -> str:
    matched: list[tuple[re.Match[str], re.Match[str]]] = [
        (method, signature) for method, signature in _methods(text)
        if signature.group("name") == target_name
        and signature.group("args") == "Ljava/lang/String;"
        and signature.group("result") == "V"
        and " static " not in " " + method.group("header") + " "
    ]
    if len(matched) != 1:
        raise RememberFileError("目标打开方法不唯一或不能使用实例参数")
    method, _signature = matched[0]
    body_lines = method.group("body").splitlines(keepends=True)
    location = _read_location(method)
    if location is None:
        raise RememberFileError("目标打开方法没有唯一的成功文件读取点")
    access, insertion = location
    # D8 may write the stream result into p1. Capture the original string
    # before opening, then restore it only after a stream was obtained.
    directive_index = next((i for i, line in enumerate(body_lines)
                            if _REGISTER_DIRECTIVE.match(line.rstrip("\r\n"))), None)
    if directive_index is None:
        raise RememberFileError("目标打开方法缺少寄存器声明")
    directive = body_lines[directive_index]
    count = int(directive.split()[-1])
    saved = _safe_capture_register(method)
    if saved is None:
        raise RememberFileError("目标打开方法没有可编码的安全临时寄存器")
    body_lines[directive_index] = re.sub(r"\d+(?=\s*(?:\r?\n)?$)", str(count + 1), directive)
    body_lines.insert(access, f"    move-object {saved}, p1\n")
    insertion += 1
    body_lines.insert(insertion + 1, "    invoke-direct {p0, " + saved + "}, " + _CLASS.search(text).group(1) + "->lazarusSaveLastRecord(Ljava/lang/String;)V\n")
    hooked = "".join(body_lines)
    return text[:method.start("body")] + hooked + text[method.end("body"):]


def _insert_restore(text: str, candidate: Candidate) -> str:
    matches = [(method, signature) for method, signature in _methods(text)
               if signature.group("name") == "onCreate" and signature.group("args") == "Landroid/os/Bundle;" and signature.group("result") == "V"]
    if len(matches) != 1:
        raise RememberFileError("onCreate 不唯一")
    method, _signature = matches[0]
    lines = method.group("body").splitlines(keepends=True)
    for index, line in enumerate(lines):
        call = _OPEN_CALL.match(line.rstrip("\r\n"))
        if (call and call.group("owner") == candidate.descriptor
                and call.group("name") == candidate.target_name
                and call.group("register") == candidate.source_register
                and _record_literal_in_block([entry.rstrip("\r\n") for entry in lines], index, candidate.source_register) == candidate.default_record):
            lines.insert(index, "    invoke-direct {p0, " + candidate.source_register + "}, " + candidate.descriptor + "->lazarusLoadLastRecord(Ljava/lang/String;)Ljava/lang/String;\n")
            lines.insert(index + 1, "    move-result-object " + candidate.source_register + "\n")
            body = "".join(lines)
            return text[:method.start("body")] + body + text[method.end("body"):]
    raise RememberFileError("默认记录打开调用已变化")


def apply(decoded: Path, plan: dict) -> dict:
    """Apply one inspected plan to an apktool directory in place.

    Callers must copy a decoded candidate before calling this method.  The plan
    is re-derived and compared before every write, so it cannot target another
    class or smuggle a different record/method into the transformation.
    """
    if not isinstance(plan, dict) or plan.get("kind") != PLAN_KIND:
        raise RememberFileError("不是可执行的记住上次文件计划")
    found = inspect(decoded)
    if not found.get("available"):
        raise RememberFileError(found.get("reason", "当前 APK 不支持此改造"))
    if plan != found["plan"]:
        raise RememberFileError("计划与当前解包 APK 的可验证结构不一致")
    target = Path(decoded) / Path(plan["class_path"])
    # class_path came from a relative rglob result.  The explicit check keeps
    # this safe even when an external caller constructed the plan manually.
    if Path(decoded).resolve() not in target.resolve().parents or target.suffix != ".smali":
        raise RememberFileError("计划中的 smali 路径无效")
    text = target.read_text(encoding="utf-8")
    if MARKER in text:
        raise RememberFileError("该 Activity 已应用记住上次文件改造")
    candidate = Candidate(
        relative_path=plan["class_path"], descriptor=plan["class_descriptor"],
        target_name=plan["target_method"], default_record=plan["default_record"], source_register=plan["source_register"],
        selection_records=tuple(plan["selection_records"]),
        selection_call_count=plan["selection_call_count"],
    )
    changed = _insert_target_hook(text, candidate.target_name)
    changed = _insert_restore(changed, candidate)
    changed += _helpers()
    target.write_text(changed, encoding="utf-8", newline="\n")
    return {"applied": True, "kind": PLAN_KIND, "rule": PLAN_KIND, "changed_file": candidate.relative_path,
            "target_method": candidate.target_name, "default_record": candidate.default_record,
            "persistence": "SharedPreferences:lazarus.remember.last.record/record",
            "audit": {"kind": PLAN_KIND, "selection_records": list(candidate.selection_records),
                      "selection_call_count": candidate.selection_call_count,
                      "save_after": "verified-file-or-asset-stream-open"}}
