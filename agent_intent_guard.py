"""Recognize newly introduced direct grants for app-private Intent handoffs."""
from collections import Counter
from dataclasses import dataclass
import re
from urllib.parse import urlsplit


INTENT = "Landroid/content/Intent;"
URI = "Landroid/net/Uri;"
GRANT_FLAGS = 0x1 | 0x2
INVOKE = re.compile(r"\{([^}]*)\},\s*(\S+)")
REGISTER_RANGE = re.compile(r"([vp])(\d{1,5})\s*\.\.\s*\1(\d{1,5})")
STRING = re.compile(r'"([^"\\]*)"')


@dataclass(frozen=True)
class ContentUri:
    authority: str


@dataclass
class Intent:
    action: str = ""
    data: ContentUri | None = None
    flags: int = 0
    package_set: bool = False
    component_set: bool = False
    known: bool = True

    def private_grant(self, package):
        return (self.known and self.action.startswith(package + ".") and self.data is not None
                and (self.data.authority == package or self.data.authority.startswith(package + "."))
                and self.flags & GRANT_FLAGS and not (self.package_set or self.component_set))


def _registers(operands):
    if match := REGISTER_RANGE.fullmatch(operands.strip()):
        prefix, start, end = match.groups()
        return [prefix + str(index) for index in range(int(start), min(int(end) + 1, int(start) + 8))]
    return [register.strip() for register in operands.split(",")]


def _content_uri(value):
    if not isinstance(value, str) or not value.startswith("content://"):
        return None
    try:
        return ContentUri(urlsplit(value).netloc)
    except ValueError:
        return None


def _invoke(target, args, package):
    receiver = args[0] if args else None
    parameters = (args[1:] + [None, None])[:2]
    value = parameters[0]
    if target == URI + "->parse(Ljava/lang/String;)" + URI:
        return _content_uri(receiver), False
    method = target.partition("->")[2].partition("(")[0]
    if method in {"startActivity", "startActivityForResult"} and "(" + INTENT in target and isinstance(value, Intent):
        return None, value.private_grant(package)
    if isinstance(receiver, Intent) and target.startswith(INTENT + "->"):
        if method == "<init>":
            receiver.action = value if isinstance(value, str) else ""
            receiver.data = parameters[1] if isinstance(parameters[1], ContentUri) else None
            receiver.package_set = False
            receiver.component_set = "Ljava/lang/Class;" in target
            receiver.known = target != INTENT + "-><init>(" + INTENT + ")V"
        elif method == "setAction":
            receiver.action = value if isinstance(value, str) else ""
        elif method in {"setData", "setDataAndType", "setDataAndNormalize", "setDataAndTypeAndNormalize"}:
            receiver.data = value if isinstance(value, ContentUri) else None
        elif method in {"setType", "setTypeAndNormalize"}:
            receiver.data = None
        elif method in {"setPackage", "setComponent", "setClass", "setClassName"}:
            # A dynamic package/component may be legitimate. Recognize only
            # literal null as clearing the selector in this limited analysis.
            if method == "setPackage":
                receiver.package_set = value != 0
            else:
                receiver.component_set = value != 0
        elif method in {"addFlags", "setFlags", "removeFlags"}:
            if type(value) is not int:
                receiver.known = False
            elif method == "addFlags":
                receiver.flags |= value
            elif method == "setFlags":
                receiver.flags = value
            else:
                receiver.flags &= ~value
        elif method not in {"addCategory", "removeCategory", "putExtra", "putExtras", "replaceExtras"}:
            receiver.known = False
        return (receiver if target.endswith(INTENT) else None), False
    if not target.startswith(INTENT + "->createChooser("):
        for argument in args:
            if isinstance(argument, Intent):
                argument.known = False
    return None, False


def direct_private_grants(source, package):
    """Count recognizable direct launches per method in straight-line smali.

    Ambiguous flows, escaped intents and unknown flags are left to runtime and
    semantic checks. No method or package name from a fixture is special-cased.
    """
    counts = Counter()
    values = {}
    pending = None
    method = None
    for raw in source.decode("utf-8", errors="replace").splitlines():
        line = raw.strip()
        if line.startswith((".method ", ".method\t")):
            method = line.partition("#")[0].split()[-1]
            values.clear()
            pending = None
        elif line == ".end method":
            method = None
        elif method and line and line[0] not in ".#":
            instruction = line.split(maxsplit=1)
            op = instruction[0]
            operands = instruction[1] if len(instruction) == 2 else ""
            if line.startswith(":") or op.startswith(("if-", "goto", "return", "throw")) or op.endswith("-switch"):
                values.clear()
                pending = None
                continue
            destination, _, operand = operands.partition(",")
            destination, operand = destination.strip(), operand.strip()
            value = None
            if op.startswith("invoke-"):
                if match := INVOKE.match(operands):
                    registers, target = match.groups()
                    pending, found = _invoke(target, [values.get(reg) for reg in _registers(registers)], package)
                    if found:
                        counts[method] += 1
                    continue
                values.clear()
            elif op == "move-result-object":
                value = pending
            elif op.startswith("move") and not op.startswith("move-result"):
                value = values.get(operand.split()[0]) if operand else None
            elif op == "new-instance" and operand.split()[:1] == [INTENT]:
                value = Intent()
            elif op.startswith("const-string"):
                if match := STRING.match(operand):
                    value = match[1]
            elif op in {"const", "const/4", "const/16", "const/high16"}:
                try:
                    value = int(operand.split()[0], 0)
                except (IndexError, ValueError):
                    pass
            values[destination] = value
            pending = None
    return counts
