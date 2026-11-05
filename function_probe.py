"""Run a user-specified UI operation on an isolated emulator and preserve evidence."""
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from io import BytesIO
import struct
from zipfile import BadZipFile, ZipFile

from bench_device import ADB, invoke, output, shell
from android_dialog import system_dialog


# These names are platform libraries.  A repair is offered only for the exact
# legacy class names below and only if `pm list libraries` proves the target
# image carries the corresponding library.  AOSP exposes the `android.test.*`
# classes from libraries whose names differ from their Java package, so a
# prefix match would make a dangerous guess.  Keep this list deliberately
# short and add a class only with source and device evidence.
SHARED_LIBRARY_CLASSES = {
    "org.apache.http.HttpHost": "org.apache.http.legacy",
    "org.apache.http.ProtocolVersion": "org.apache.http.legacy",
    "android.test.InstrumentationTestRunner": "android.test.runner",
    "android.test.AndroidTestCase": "android.test.base",
    "android.test.mock.MockContext": "android.test.mock",
}
MAX_LIBRARY_LISTING_BYTES = 128 * 1024
MAX_LIBRARY_CANDIDATES = 24
MAX_LIBRARY_JAR_BYTES = 32 * 1024 * 1024
MAX_DEX_FILES = 4
MAX_DEX_BYTES = 24 * 1024 * 1024
_LIBRARY_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,99}")
_FRAMEWORK_JAR = re.compile(r"/system/framework/[A-Za-z0-9_.-]{1,100}\.jar")
_CLASS_NAME = re.compile(r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+")
# These are boot-class-path/framework implementation jars, not PackageManager
# shared-library candidates.  A missing app class must never trigger scanning
# the complete framework as a speculative repair source.
BOOT_CLASS_PATH_JARS = frozenset({
    "/system/framework/framework.jar", "/system/framework/core-oj.jar",
    "/system/framework/core-libart.jar", "/system/framework/ext.jar",
    "/system/framework/okhttp.jar", "/system/framework/bouncycastle.jar",
})


def _device_library_jars(listings):
    """Parse only `pm list libraries -f` records for framework JARs."""
    if not isinstance(listings, str) or len(listings.encode("utf-8", "ignore")) > MAX_LIBRARY_LISTING_BYTES:
        return ()
    records = []
    pending = None
    inline = re.compile(r"^library:([A-Za-z][A-Za-z0-9_.-]{0,99})\s+\(?(?:jar\s+)?file:\s*(/system/framework/[A-Za-z0-9_.-]{1,100}\.jar)\)?\s*$")
    dumpsys = re.compile(r"^\s+([A-Za-z][A-Za-z0-9_.-]{0,99})\s+->\s+\(jar\)\s+(/system/framework/[A-Za-z0-9_.-]{1,100}\.jar)\s*$")
    for line in listings.splitlines():
        line = line.rstrip()
        both = inline.fullmatch(line)
        if both:
            records.append(both.groups())
            pending = None
            continue
        dumped = dumpsys.fullmatch(line)
        if dumped:
            records.append(dumped.groups())
            pending = None
            continue
        named = re.fullmatch(r"library:([A-Za-z][A-Za-z0-9_.-]{0,99})", line)
        if named:
            pending = named.group(1)
            continue
        jar = re.fullmatch(r"\s+(?:file:)?(/system/framework/[A-Za-z0-9_.-]{1,100}\.jar)", line)
        if pending and jar:
            records.append((pending, jar.group(1)))
        pending = None
    unique = {(name, path) for name, path in records
              if _LIBRARY_NAME.fullmatch(name) and _FRAMEWORK_JAR.fullmatch(path)
              and path not in BOOT_CLASS_PATH_JARS}
    return tuple(sorted(unique)) if len(unique) <= MAX_LIBRARY_CANDIDATES else ()


def _listed_library_names(listings):
    return {name for name, _ in _device_library_jars(listings)} | {
        match.group(1) for match in re.finditer(r"(?m)^library:([A-Za-z][A-Za-z0-9_.-]{0,99})\s*$", listings)
    } if isinstance(listings, str) else set()


def _read_uleb128(data, offset):
    value = 0
    for shift in range(0, 35, 7):
        if offset >= len(data):
            raise ValueError("Truncated ULEB128")
        part = data[offset]
        offset += 1
        value |= (part & 0x7f) << shift
        if not part & 0x80:
            return value, offset
    raise ValueError("Invalid ULEB128")


def _dex_defines_class(data, wanted_descriptor):
    """Inspect DEX type/class_def tables, never a raw string scan."""
    if len(data) < 112 or not data.startswith((b"dex\n035\0", b"dex\n037\0", b"dex\n038\0", b"dex\n039\0", b"dex\n040\0", b"dex\n041\0")):
        raise ValueError("Unsupported DEX")
    file_size, header_size, endian = struct.unpack_from("<III", data, 32)
    if file_size != len(data) or header_size != 112 or endian != 0x12345678:
        raise ValueError("Malformed DEX header")
    string_count, string_off, type_count, type_off = struct.unpack_from("<IIII", data, 56)
    class_count, class_off = struct.unpack_from("<II", data, 96)
    if string_count > 250000 or type_count > 250000 or class_count > 250000:
        raise ValueError("DEX table exceeds limit")
    if string_off + string_count * 4 > len(data) or type_off + type_count * 4 > len(data) or class_off + class_count * 32 > len(data):
        raise ValueError("DEX table out of range")
    cache = {}
    def descriptor(type_index):
        if type_index >= type_count:
            raise ValueError("Invalid type index")
        string_index = struct.unpack_from("<I", data, type_off + type_index * 4)[0]
        if string_index >= string_count:
            raise ValueError("Invalid string index")
        if string_index not in cache:
            offset = struct.unpack_from("<I", data, string_off + string_index * 4)[0]
            _, offset = _read_uleb128(data, offset)
            end = data.find(b"\0", offset)
            if end < offset or end - offset > 1024:
                raise ValueError("Invalid DEX string")
            cache[string_index] = data[offset:end].decode("utf-8", "strict")
        return cache[string_index]
    for index in range(class_count):
        type_index = struct.unpack_from("<I", data, class_off + index * 32)[0]
        if descriptor(type_index) == wanted_descriptor:
            return True
    return False


def _jar_defines_class(blob, wanted_descriptor):
    if not isinstance(blob, bytes) or len(blob) > MAX_LIBRARY_JAR_BYTES:
        raise ValueError("JAR exceeds limit")
    with ZipFile(BytesIO(blob)) as archive:
        dex_entries = [entry for entry in archive.infolist() if re.fullmatch(r"classes(?:[2-9]|[1-9][0-9]+)?\.dex", entry.filename)]
        if not dex_entries or len(dex_entries) > MAX_DEX_FILES or sum(entry.file_size for entry in dex_entries) > MAX_DEX_BYTES:
            raise ValueError("DEX inventory exceeds limit")
        for entry in dex_entries:
            if entry.file_size > MAX_DEX_BYTES or entry.compress_size > MAX_DEX_BYTES:
                raise ValueError("DEX exceeds limit")
            if _dex_defines_class(archive.read(entry), wanted_descriptor):
                return True
    return False


def discover_shared_library(missing_class, listings, read_jar):
    """Return a uniquely proven device shared library for one missing class.

    `read_jar(path)` must be supplied by the device adapter and is called only
    with a validated `/system/framework/*.jar` path listed by PackageManager.
    Invalid output, missing DEX, duplicate class ownership, oversized data and
    every parsing failure close the discovery attempt.
    """
    known = resolve_platform_library(missing_class, listings)
    if known:
        return known
    if not isinstance(missing_class, str) or not _CLASS_NAME.fullmatch(missing_class) or not callable(read_jar):
        return None
    wanted = "L" + missing_class.replace(".", "/") + ";"
    matches = []
    try:
        for library, path in _device_library_jars(listings):
            if _jar_defines_class(read_jar(path), wanted):
                matches.append(library)
                if len(matches) > 1:
                    return None
    except (BadZipFile, OSError, ValueError, struct.error, UnicodeError):
        return None
    return matches[0] if len(matches) == 1 else None


def extract_missing_class(text):
    """Extract one JVM class name from an app PID's crash text.

    The caller owns PID scoping.  This parser does not inspect arbitrary files
    or execute anything, and requires a standard Android missing-class form.
    """
    if not isinstance(text, str):
        return None
    patterns = (
        r"NoClassDefFoundError: Failed resolution of: L([A-Za-z_$][\w$]*(?:/[A-Za-z_$][\w$]*)+);",
        r"ClassNotFoundException: Didn't find class \"([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+)\"",
        r"NoClassDefFoundError: L([A-Za-z_$][\w$]*(?:/[A-Za-z_$][\w$]*)+);",
    )
    for pattern in patterns:
        found = re.search(pattern, text)
        if found:
            return found.group(1).replace("/", ".")
    return None


def resolve_platform_library(missing_class, available_libraries):
    """Return the verified shared-library remediation, or None.

    `available_libraries` is the raw line-oriented result of ``pm list
    libraries``.  The exact `library:` spelling prevents a package name in an
    unrelated system message from being treated as device evidence.
    """
    if not isinstance(missing_class, str) or not isinstance(available_libraries, str):
        return None
    library = SHARED_LIBRARY_CLASSES.get(missing_class)
    if missing_class.startswith("org.apache.http.") and _CLASS_NAME.fullmatch(missing_class):
        library = "org.apache.http.legacy"
    if library and library in _listed_library_names(available_libraries):
        return library
    return None


def probe(serial,pkg,task,dest):
    dest.mkdir()
    path = "/data/local/tmp/reviver-task.xml"
    waited_for_system_ui = False
    def snapshot(label):
        shell(serial,"rm","-f",path,timeout=5)
        shell(serial,"uiautomator","dump",path,timeout=15)
        result=invoke([ADB,"-s",serial,"exec-out","cat",path],5)
        if result.returncode:
            raise RuntimeError(output(result))
        xml=result.stdout
        (dest/(label+".xml")).write_bytes(xml)
        shot=invoke([ADB,"-s",serial,"exec-out","screencap","-p"],8).stdout
        if shot.startswith(b"\x89PNG"):
            (dest/(label+".png")).write_bytes(shot)
        return ET.fromstring(xml)
    def is_match(node,value):
        return value in (node.get("text"),node.get("content-desc"),node.get("resource-id"))
    def tap(bounds_text):
        bounds=list(map(int,re.findall(r"\d+",bounds_text)))
        if len(bounds)!=4 or bounds[0]>=bounds[2] or bounds[1]>=bounds[3]:
            raise ValueError("控件位置无效")
        shell(serial,"input","tap",str((bounds[0]+bounds[2])//2),str((bounds[1]+bounds[3])//2),timeout=10)
    def observe(label, value):
        nonlocal waited_for_system_ui
        deadline = time.monotonic() + 25
        next_label = label
        for _ in range(6):
            try:
                tree = snapshot(next_label)
            except (ET.ParseError, RuntimeError, subprocess.TimeoutExpired) as exc:
                (dest/(label+"-observation.txt")).write_text(str(exc)[-500:],encoding="utf-8")
            else:
                dialog, button, _ = system_dialog(tree)
                if dialog in ("system-ui-anr", "other-anr"):
                    if waited_for_system_ui or dialog != "system-ui-anr":
                        return None
                    # This probe runs on a disposable lab device. Leave other ANRs for diagnosis.
                    for suffix in (".xml", ".png"):
                        evidence = dest/(next_label+suffix)
                        if evidence.is_file():
                            evidence.replace(dest/(label+"-system-ui-wait"+suffix))
                    tap(button["bounds"])
                    waited_for_system_ui = True
                elif dialog == "legacy-target-notice":
                    if label != "before" or next_label != "before":
                        return None
                    tap(button["bounds"])
                    next_label = "after-legacy-notice"
                else:
                    matches = [n for n in tree.iter("node") if n.get("package")==pkg and is_match(n,value)]
                    if matches and (label == "after" or len(matches) == 1):
                        return matches[0]
            if time.monotonic() >= deadline:
                break
            time.sleep(1)
        return None

    node=observe("before",task["tap"])
    if node is None:
        return {"result":"insufficient","system_ui_waited":waited_for_system_ui,
                "message":"找不到指定控件。请核对文案、系统弹窗和应用当前页面。"}
    activities=shell(serial,"dumpsys","activity","top","-c")
    owner=re.findall(r"(?m)^\s*ACTIVITY "+re.escape(pkg)+r"/\S+ [^\r\n]*?\bpid=(\d+)",activities)
    # Only clear the crash buffer on our private lab AVD before this individual action.
    invoke([ADB,"-s",serial,"logcat","-b","crash","-c"])
    tap(node.get("bounds",""))
    time.sleep(1)
    after=observe("after",task["expect"])
    pid_filter=["--pid",owner[-1]] if owner else []
    crash=output(invoke([ADB,"-s",serial,"logcat","-b","crash","-d",*pid_filter]))
    process=re.search(r"AndroidRuntime:\s+Process: ([\w.:]+), PID: (\d+)",crash)
    if process and (process.group(2) in owner or process.group(1).split(":")[0]==pkg) and "FATAL EXCEPTION" in crash:
        (dest/"crash.txt").write_text(crash,encoding="utf-8")
        missing_class=extract_missing_class(crash)
        exception=re.search(r"AndroidRuntime:\s+(?:Caused by: )?(android\.os\.FileUriExposedException)\b",crash)
        failure={"result":"failed","system_ui_waited":waited_for_system_ui,"message":"核心操作触发应用崩溃",
                 "missing_class":missing_class}
        if exception:
            failure.update(exception=exception.group(1),message="共享文件时触发 FileUriExposedException")
        return failure
    if after is not None:
        return {"result":"passed","system_ui_waited":waited_for_system_ui,
                "message":"操作后在目标应用内找到预期内容："+task["expect"]}
    return {"result":"insufficient","system_ui_waited":waited_for_system_ui,
            "message":"操作后未找到预期内容，详见界面树。"}
