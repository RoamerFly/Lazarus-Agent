"""APK-only compatibility repair. An exported candidate still requires runtime validation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import struct
import subprocess
import tempfile
from zipfile import ZipFile

from native_elf import elf_uses_textrel, library_member
from offline_kit import discover_java_home, java_environment
from storage_paths import configure_environment

INSTALL_FLOOR = {34: 23, 35: 24, 36: 24}
ANDROID_TARGET_SDK = 0x01010270


def update_manifest(data, target):
    """Edit a typed Android targetSdkVersion in a binary XML manifest, preserving other bytes."""
    result = bytearray(data)
    if struct.unpack_from("<HHI", data) != (3, 8, len(data)):
        raise ValueError("Unsupported Android manifest encoding")
    offset, ids, changes = 8, (), []
    while offset < len(data):
        kind, hdr, size = struct.unpack_from("<HHI", data, offset)
        if hdr < 8 or size < hdr or offset + size > len(data):
            raise ValueError("Invalid manifest chunk")
        if kind == 0x180:
            ids = struct.unpack_from(f"<{(size-hdr)//4}I", data, offset+hdr)
        if kind == 0x102:
            ext = offset + hdr
            first, step, count = struct.unpack_from("<HHH", data, ext+8)
            if step < 20 or ext+first+step*count > offset+size:
                raise ValueError("Invalid attribute list")
            for i in range(count):
                pos = ext + first + i*step
                name, raw = struct.unpack_from("<II", data, pos+4)
                if name >= len(ids) or ids[name] != ANDROID_TARGET_SDK:
                    continue
                if data[pos+15] != 0x10 or raw != 0xffffffff:
                    raise ValueError("Unsupported SDK attribute representation")
                old = struct.unpack_from("<I", data, pos+16)[0]
                if old >= target:
                    raise ValueError("Target SDK is already above the installation floor")
                changes.append(old)
                struct.pack_into("<I", result, pos+16, target)
        offset += size
    if len(changes) != 1:
        raise ValueError("Expected exactly one explicit integer targetSdkVersion")
    return bytes(result), changes[0]


def run(command, env=None, cwd=None):
    proc = subprocess.run([str(arg) for arg in command], capture_output=True, env=env,
                          encoding="utf-8", errors="replace", timeout=180, cwd=cwd,
                          creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
    if proc.returncode:
        raise RuntimeError((proc.stderr or proc.stdout).strip())
    return proc.stdout


def _signing_home() -> Path:
    """Use the existing normal-install key, never export it to portable data."""
    paths = configure_environment()
    home = paths.data / "signing"
    legacy_root = os.environ.get("LOCALAPPDATA", "").strip()
    legacy = Path(legacy_root) / "LegacyApkReviver" / "signing" if legacy_root else None
    storage_explicit = bool(os.environ.get("LAZARUS_STORAGE_ROOT") or os.environ.get("LAZARUS_PORTABLE") or os.environ.get("LAZARUS_PORTABLE_ROOT"))
    # Upgrading the normal desktop install keeps its established signing
    # identity.  Portable/explicit storage creates a separate identity rather
    # than exporting a user's private key into the portable package.
    if not storage_explicit and legacy and (legacy / "lab.p12").is_file() and (legacy / "lab.pass").is_file():
        return legacy
    return home


def test_key(env):
    home = _signing_home()
    home.mkdir(parents=True, exist_ok=True)
    key, password = home/"lab.p12", home/"lab.pass"
    if not password.exists():
        with password.open("x", encoding="ascii") as f:
            f.write(secrets.token_urlsafe(32))
    env["REVIVER_KEYPASS"] = password.read_text(encoding="ascii")
    if not key.exists():
        java_home = discover_java_home()
        keytool = java_home / "bin" / "keytool.exe" if java_home else "keytool"
        run([keytool, "-genkeypair", "-storetype", "PKCS12", "-keystore", key,
             "-storepass:env", "REVIVER_KEYPASS", "-alias", "reviver", "-keyalg", "RSA",
             "-keysize", "3072", "-validity", "3650", "-dname", "CN=Legacy APK Reviver Local Test"], env)
    return key


def _tool_temp_root() -> Path:
    """Return a Windows tool-safe staging path (aapt/zipalign reject Unicode paths)."""
    candidates = [os.environ.get("REVIVER_TOOL_TEMP", ""), os.environ.get("TEMP", ""), r"C:\Temp"]
    for candidate in candidates:
        if candidate and candidate.isascii():
            root = Path(candidate) / "Lazarus-Agent-work"
            root.mkdir(parents=True, exist_ok=True)
            return root
    raise RuntimeError("Android build tools require an ASCII TEMP path; set REVIVER_TOOL_TEMP to an ASCII directory")


def repair(source, output, api, tools, rule="installation-floor", remove_abis=(), modernize_crt=False):
    if api not in INSTALL_FLOOR:
        raise ValueError("Unsupported Android API")
    if source.resolve() == output.resolve() or output.exists():
        raise ValueError("Use a new output path")
    rules=(rule,) if isinstance(rule,str) else tuple(rule)
    if not rules or any(item not in {"installation-floor","platform-http-library","drop-incomplete-abi","embedded-native-payload","arm-crt-floor"} for item in rules):
        raise ValueError("Unknown repair rule")
    if len(set(rules)) != len(rules):
        raise ValueError("Duplicate repair rule")
    if "arm-crt-floor" in rules and len(rules)!=1:
        raise ValueError("arm-crt-floor requires a standalone repair pass")
    remove_abis=tuple(sorted(set(remove_abis)))
    if ("drop-incomplete-abi" in rules) != bool(remove_abis):
        raise ValueError("ABI removal requires the drop-incomplete-abi rule and explicit ABI names")
    if "embedded-native-payload" in rules and len(rules) != 1:
        raise ValueError("embedded-native-payload must preserve the original manifest and cannot be combined with other rules")
    if any(not re.fullmatch(r"[A-Za-z0-9_.+-]+", abi) for abi in remove_abis):
        raise ValueError("Invalid ABI name")
    output.parent.mkdir(parents=True, exist_ok=True)
    # Keep the transient unsigned/aligned paths ASCII even when the user chose
    # an APK from a Chinese folder.  Python handles the final rename itself.
    with tempfile.TemporaryDirectory(dir=_tool_temp_root()) as directory:
        work = Path(directory)
        unsigned, aligned, signed = [work/name for name in ("unsigned.apk","aligned.apk","signed.apk")]
        with ZipFile(source) as src:
            names = src.namelist()
            if len(names) != len(set(names)):
                raise ValueError("Duplicate ZIP entries")
            original_manifest = src.read("AndroidManifest.xml")
            replacements={}
            if "embedded-native-payload" in rules:
                # This rule deliberately delegates discovery to the narrowly
                # audited inspector.  The repairer verifies every returned path
                # again and never guesses from a similar filename.
                try:
                    from asset_native_repair import replacement_map
                except ImportError as exc:
                    raise ValueError("No audited embedded-native-payload mapping is available") from exc
                mapped=replacement_map(source)
                if not isinstance(mapped,dict) or not mapped:
                    raise ValueError("No audited embedded-native-payload replacement mapping")
                names=set(src.namelist())
                verified={}
                payload_sources={}
                for top, asset in mapped.items():
                    top_member=library_member(top) if isinstance(top,str) else None
                    asset_match=(re.fullmatch(r"assets/libs/([^/]+)/([^/]+\.so)",asset)
                                 if isinstance(asset,str) else None)
                    if (not top_member or not asset_match or top not in names or asset not in names or
                            top_member[0] != asset_match.group(1)):
                        raise ValueError("Unsafe embedded-native-payload mapping")
                    payload=src.read(asset)
                    if not payload.startswith(b"\x7fELF"):
                        raise ValueError("Embedded native payload is not ELF: "+asset)
                    verified[top]=payload
                    payload_sources[top]=asset
                replacements=verified
                manifest=original_manifest
                changes={"replaced_native_libraries":sorted(replacements),
                         "payload_sources":payload_sources}
                if modernize_crt:
                    from arm_crt_pic import PROFILES, rewrite
                    affected={}
                    for entry in src.infolist():
                        if not library_member(entry.filename):
                            continue
                        data=replacements.get(entry.filename)
                        if data is None:
                            data=src.read(entry)
                        if elf_uses_textrel(data):
                            affected[entry.filename]=data
                    if affected and all(hashlib.sha256(data).hexdigest() in PROFILES for data in affected.values()):
                        manifest,previous=update_manifest(original_manifest,INSTALL_FLOOR[api])
                        replacements.update({name:rewrite(data) for name,data in affected.items()})
                        changes.update(before=previous,after=INSTALL_FLOOR[api],pic_libraries=sorted(affected))
            elif rules==("arm-crt-floor",):
                from arm_crt_pic import rewrite
                for item in src.infolist():
                    if library_member(item.filename):
                        library=src.read(item)
                        if elf_uses_textrel(library):
                            replacements[item.filename]=rewrite(library)
                if not replacements:
                    raise ValueError("No ARM CRT text relocations to repair")
                manifest,previous=update_manifest(original_manifest,INSTALL_FLOOR[api])
                changes={"before":previous,"after":INSTALL_FLOOR[api],"pic_libraries":sorted(replacements)}
            elif "installation-floor" in rules:
                textrel=[item.filename for item in src.infolist()
                         if library_member(item.filename) and elf_uses_textrel(src.read(item))]
                if textrel and INSTALL_FLOOR[api] >= 23:
                    raise ValueError("Refusing targetSdk upgrade: DT_TEXTREL blocks modern linker: "+", ".join(textrel))
                manifest, previous = update_manifest(original_manifest, INSTALL_FLOOR[api])
                changes={"before":previous,"after":INSTALL_FLOOR[api]}
            elif "platform-http-library" in rules:
                from runtime_patch import declare_legacy_http, LIBRARY
                manifest=declare_legacy_http(original_manifest)
                changes={"added_library":LIBRARY}
            else:
                manifest=original_manifest
                changes={}
            if "drop-incomplete-abi" in rules:
                remaining={library_member(item.filename)[0] for item in src.infolist()
                           if library_member(item.filename) and library_member(item.filename)[0] not in remove_abis}
                if not remaining:
                    raise ValueError("Refusing to remove every native ABI")
                changes["removed_abis"]=list(remove_abis)
            with ZipFile(unsigned,"w") as dst:
                for item in src.infolist():
                    if re.fullmatch(r"META-INF/(?:MANIFEST\.MF|[^/]+\.(?:SF|RSA|DSA|EC)|SIG-[^/]+)", item.filename, re.I):
                        continue
                    member=library_member(item.filename)
                    if member and member[0] in remove_abis:
                        continue
                    if item.filename == "AndroidManifest.xml":
                        dst.writestr(item, manifest)
                    elif item.filename in replacements:
                        dst.writestr(item, replacements[item.filename])
                    else:
                        with src.open(item) as a, dst.open(item,"w") as b:
                            shutil.copyfileobj(a,b)
        run([(tools/"zipalign.exe").resolve(), "-p", "4", unsigned.name, aligned.name],cwd=work)
        env = java_environment(os.environ.copy())
        key = test_key(env)
        java_home = discover_java_home()
        java = java_home / "bin" / "java.exe" if java_home else "java"
        signer = [java,"-jar",tools/"lib/apksigner.jar"]
        run(signer+["sign","--ks", key, "--ks-pass","env:REVIVER_KEYPASS","--out",signed,aligned],env)
        run(signer+["verify","--min-sdk-version",str(api),"--verbose", signed],env)
        with signed.open("rb") as f:
            digest = hashlib.file_digest(f,"sha256").hexdigest()
        # Signing tools need an ASCII path, which may be on a different drive
        # from a portable job. Publish only a complete, hash-checked copy.
        with tempfile.TemporaryDirectory(dir=output.parent) as staging:
            staged=Path(staging)/"verified.apk"
            shutil.copyfile(signed,staged)
            with staged.open("rb") as f:
                if hashlib.file_digest(f,"sha256").hexdigest()!=digest:
                    raise ValueError("复制后的候选 APK 哈希不一致")
            if os.name=="nt":
                staged.rename(output)
            else:
                os.link(staged,output)
    label=rules[0] if len(rules)==1 else "+".join(rules)
    return {"rule":label, "rules":list(rules), "api":api, **changes,
            "candidate":str(output),"sha256":digest,"runtime":"not-tested"}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("apk", type=Path)
    p.add_argument("--api", type=int, choices=INSTALL_FLOOR, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--build-tools", type=Path, required=True)
    p.add_argument("--abis",default="",help="Target's ro.product.cpu.abilist (required for APKs with native libraries)")
    args=p.parse_args()
    from diagnose import assess, inspect_package
    facts=inspect_package(args.apk,args.build_tools)
    abis=[abi.strip() for abi in args.abis.split(",") if abi.strip()]
    if facts["native_abis"] and not abis:
        p.error("Native libraries present: provide --abis from the target device before attempting repair")
    assessment=assess(facts,args.api,abis)
    if assessment["suggested_action"]=="report-limit":
        p.error("Unsupported on this target: "+json.dumps(assessment["static_findings"]))
    print(json.dumps(repair(args.apk,args.out,args.api,args.build_tools),ensure_ascii=False,indent=2))
