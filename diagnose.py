"""Read compatibility evidence without installing or modifying an APK."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
from zipfile import ZipFile

from native_elf import elf_uses_textrel, library_member
from reviver import INSTALL_FLOOR, run


def inspect_package(apk, tools):
    # Some Windows SDK binaries decode non-ASCII arguments using the wrong code page.
    with tempfile.TemporaryDirectory() as temp:
        shutil.copyfile(apk,Path(temp)/"input.apk")
        summary = run([(tools/"aapt.exe").resolve(),"dump","badging","input.apk"],cwd=temp)
    def field(pattern, default=None):
        m = re.search(pattern, summary, re.MULTILINE)
        return m.group(1) if m else default
    pkg = field(r"^package: name='([^']+)'")
    if not pkg:
        raise ValueError("Missing Android package name")
    with ZipFile(apk) as z:
        libraries = {}
        textrel = []
        for item in z.infolist():
            member = library_member(item.filename)
            if not member:
                continue
            abi, name = member
            libraries.setdefault(abi, []).append(name)
            if elf_uses_textrel(z.read(item)):
                textrel.append(item.filename)
        libraries = {abi: sorted(names) for abi, names in sorted(libraries.items())}
        abis = list(libraries)
    minimum = int(field(r"^sdkVersion:'(\d+)'", "1"))
    with apk.open("rb") as f:
        digest = hashlib.file_digest(f,"sha256").hexdigest()
    return {"package":pkg, "version":field(r"^package: .*?versionName='([^']*)'"),
            "label":field(r"^application-label:'(.*)'",pkg),
            "min_sdk":minimum, "target_sdk":int(field(r"^targetSdkVersion:'(\d+)'",str(minimum))),
            "launcher":field(r"^launchable-activity: name='([^']+)'"),
            "native_abis":abis,"native_libraries":libraries,
            "textrel_libraries":sorted(textrel),"sha256":digest}


def manifest_surface(apk, tools):
    """Record explicit permission/component declarations from the compiled manifest."""
    with tempfile.TemporaryDirectory() as temp:
        shutil.copyfile(apk, Path(temp) / "input.apk")
        text = run([(tools / "aapt.exe").resolve(), "dump", "xmltree", "input.apk", "AndroidManifest.xml"], cwd=temp)
    permissions, components = [], []
    stack = []
    for line in text.splitlines():
        indent = len(line) - len(line.lstrip())
        element = re.match(r"\s*E: ([\w-]+)", line)
        if element:
            while stack and stack[-1][0] >= indent:
                stack.pop()
            tag = element[1]
            parent = stack[-1][1] if stack else None
            record = None
            if parent == "manifest" and tag in {"uses-permission", "uses-permission-sdk-23", "uses-permission-sdk-m"}:
                record = {"type": tag}
                permissions.append(record)
            elif parent == "application" and tag in {"activity", "activity-alias", "service", "receiver", "provider"}:
                record = {"type": tag}
                components.append(record)
            stack.append((indent, tag, record))
        elif stack and stack[-1][2] is not None:
            attr = re.match(r"\s*A: [\w.-]+:(name|exported|enabled|permission|readPermission|writePermission|authorities|targetActivity|maxSdkVersion)\(0x[\da-f]+\)=(.+)", line)
            if attr:
                stack[-1][2][attr[1]] = attr[2].split(" (Raw:", 1)[0].strip('"')
    return {"permissions": permissions, "components": components}


def manifest_changes(source, candidate, tools):
    before, after = [manifest_surface(apk, tools) for apk in (source, candidate)]
    return {name: {"removed": [row for row in before[name] if row not in after[name]],
                   "added": [row for row in after[name] if row not in before[name]]}
            for name in before}


def abi_pruning_plan(facts, device_abis, missing_library):
    """Plan removal of ABI directories only when an exact safe fallback exists.

    A 64-bit device is *not* treated as automatically able to run a 32-bit
    ABI.  The supplied device ABI list must explicitly contain the retained ABI.
    """
    if not re.fullmatch(r"lib[A-Za-z0-9_.+-]+\.so", missing_library or ""):
        return None
    libraries = facts.get("native_libraries", {})
    supported = [abi for abi in device_abis if abi in libraries and missing_library in libraries[abi]]
    if not supported:
        return None
    # Keep an ABI only if it includes the precise failed library.  Remove only
    # directories demonstrably incomplete for that same load, never add/copy
    # a library or invent a 64-bit variant.
    remove = sorted(abi for abi, names in libraries.items() if missing_library not in names)
    if not remove or len(remove) == len(libraries):
        return None
    return {"missing_library":missing_library, "retain_abis":supported,
            "remove_abis":remove, "evidence":"runtime-missing-library"}


def assess(facts, api, device_abis):
    issues = []
    if facts["min_sdk"] > api:
        issues.append({"code":"requires-newer-platform","remediable":False,
                       "evidence":f"minSdk {facts['min_sdk']} > device API {api}"})
    if facts["native_abis"] and device_abis and not set(device_abis).intersection(facts["native_abis"]):
        issues.append({"code":"native-abi-mismatch","remediable":False,
                       "evidence":f"lib/: {facts['native_abis']}; device: {device_abis}"})
    if facts["target_sdk"] < INSTALL_FLOOR[api]:
        if facts.get("textrel_libraries") and INSTALL_FLOOR[api] >= 23:
            issues.append({"code":"native-textrel-blocks-target-upgrade","remediable":False,
                           "evidence":"DT_TEXTREL in "+", ".join(facts["textrel_libraries"]) +
                                      f"; targetSdk >= {INSTALL_FLOOR[api]} is rejected by modern linker"})
        else:
            issues.append({"code":"installation-floor","remediable":True,
                           "evidence":f"targetSdk {facts['target_sdk']} < install floor {INSTALL_FLOOR[api]}"})
    if any(not issue["remediable"] for issue in issues):
        action = "report-limit"
    elif issues:
        action = "try-installation-floor-repair"
    else:
        action = "test-original"
    return {"api":api,"device_abis":device_abis,"static_findings":issues,
            "suggested_action":action,"runtime":"not-tested"}


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("apk",type=Path)
    parser.add_argument("--api",type=int,choices=INSTALL_FLOOR,required=True)
    parser.add_argument("--abis",default="",help="Comma-separated ABIs reported by the device")
    parser.add_argument("--build-tools",type=Path,required=True)
    args=parser.parse_args()
    package=inspect_package(args.apk,args.build_tools)
    print(json.dumps({"package":package,"assessment":assess(package,args.api,list(filter(None,args.abis.split(","))))},
                     ensure_ascii=False,indent=2))
