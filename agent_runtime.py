"""Return bounded Java failure evidence from an isolated Android crash buffer."""
import re

from function_probe import extract_missing_class


_LINE = re.compile(r"^(\d\d-\d\d\s+\d\d:\d\d:\d\d\.\d+)\s+(\d+)\s+\d+\s+[EF]\s+AndroidRuntime\s*:\s?(.*)$")
_PROCESS = re.compile(r"^Process: ([\w.:]+), PID: (\d+)\s*$", re.ASCII)
_EXCEPTION = re.compile(r"^(?:Caused by: )?([\w.$]+(?:Exception|Error|Throwable))(?::|\s*$)", re.ASCII)
_METHOD = re.compile(r"No (?:interface |virtual |static |direct )?method "
                     r"([\w$<>-]+\([\w/;$\[]*\)[\w/;$\[]+) in class (L[\w/$]+;)", re.ASCII)
_FRAME = re.compile(r"^at ([\w.$-]+)\([^)]*?:(\d+)\)$", re.ASCII)


def java_crashes(text, package):
    """Index crash summaries by the timestamp and PID of the fatal record."""
    events, pending = {}, {}
    for line in text.splitlines():
        entry = _LINE.match(line)
        if not entry:
            continue
        stamp, pid, message = entry.groups()
        message = message.strip()
        if message.startswith("FATAL EXCEPTION:"):
            pending[pid] = (stamp, {})
            continue
        if pid not in pending:
            continue
        started, event = pending[pid]
        process = _PROCESS.fullmatch(message)
        if process:
            name, declared_pid = process.groups()
            if name.split(":", 1)[0] == package and declared_pid == pid:
                event.update(process=name, exception_types=[], app_frames=[])
            else:
                pending.pop(pid)
            continue
        if "process" not in event:
            continue
        exception = _EXCEPTION.match(message)
        if exception:
            kind = exception.group(1)
            if kind not in event["exception_types"] and len(event["exception_types"]) < 4:
                event["exception_types"].append(kind[:160])
            if "missing_class" not in event and (missing := extract_missing_class(message)):
                event["missing_class"] = missing[:200]
            if kind == "java.lang.NoSuchMethodError" and (method := _METHOD.search(message)):
                signature, owner = method.groups()
                event["missing_method"] = {"owner": owner[:160], "signature": signature[:240]}
            events[(started, pid)] = event
        elif len(event["app_frames"]) < 3 and (frame := _FRAME.fullmatch(message)):
            symbol, line_number = frame.groups()
            if symbol.startswith(package + "."):
                event["app_frames"].append({"symbol": symbol[:200], "line": int(line_number[:9])})
    return events
