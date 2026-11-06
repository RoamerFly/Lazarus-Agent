"""Prepare, replay, or audit the frozen cohort using the packaged source ZIP."""
from argparse import ArgumentParser
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
from tempfile import TemporaryDirectory
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.replay_f01_recorded_cohort import digest, read, replay

WORKSPACE_OUTPUTS = {"build", "dist"}


def source_members(archive):
    """Reject ambiguous or non-file members before writing anything."""
    files = {}
    seen = set()
    for member in archive.infolist():
        path = member.filename.removesuffix("/")
        pieces = path.split("/")
        kind = stat.S_IFMT(member.external_attr >> 16)
        if (any(piece in {"", ".", ".."} or piece.endswith((".", " "))
                or re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(?:\..*)?", piece)
                for piece in pieces)
                or any(character in path for character in '\\:<>"|?*') or any(ord(ch) < 32 for ch in path)
                or pieces[0].casefold() in WORKSPACE_OUTPUTS | {".git"}
                or path.casefold() in seen
                or kind not in {0, stat.S_IFDIR if member.is_dir() else stat.S_IFREG}):
            raise ValueError("源码 ZIP 包含不安全或重复的路径")
        seen.add(path.casefold())
        if not member.is_dir():
            files[path] = member
    return files


def verify_workspace(workspace, archive, files, build):
    actual = set()
    for path in workspace.rglob("*"):
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            raise ValueError("离线工作区包含链接")
        relative = path.relative_to(workspace)
        if relative.parts[0] not in WORKSPACE_OUTPUTS and path.is_file():
            actual.add(relative.as_posix())
    if actual != set(files):
        raise ValueError("离线工作区源码文件清单已变化")
    for name, member in files.items():
        with archive.open(member) as stream:
            expected = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest(workspace / name) != expected:
            raise ValueError(f"离线工作区源码已变化 {name}")
    if (read(workspace / "build/local-exe-selftest-receipt.json") != build
            or digest(workspace / "dist/Lazarus-Agent.exe") != build["exe_sha256"]):
        raise ValueError("离线工作区 EXE 或构建回执已变化")


def prepare_workspace(package, workspace, exe):
    build = read(package / "本地构建回执.json")
    receipt = read(package / "同版材料校验回执.json")
    version = build["version"]
    if (not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version)
            or receipt["app_version"] != version or build["self_test_exit"] != 0
            or any(receipt[key] != build[key] for key in ("source_commit", "exe_sha256"))
            or digest(exe) != build["exe_sha256"]):
        raise ValueError("评审包的便携 EXE 或构建回执不一致")
    source = package / f"F01-源代码-v{version}.zip"
    if digest(source) != receipt["source_archive_sha256"]:
        raise ValueError("评审包的源码 ZIP 哈希不一致")
    with ZipFile(source) as archive:
        if archive.comment.decode("ascii") != build["source_commit"]:
            raise ValueError("源码 ZIP 的提交与评审包不一致")
        files = source_members(archive)
        if workspace.exists():
            if not workspace.is_dir():
                raise ValueError("请选择一个离线工作区目录")
            verify_workspace(workspace, archive, files, build)
        else:
            workspace.parent.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(dir=workspace.parent, prefix="f01-source-") as directory:
                staging = Path(directory) / "source"
                staging.mkdir()
                archive.extractall(staging, files.values())
                (staging / "build").mkdir()
                (staging / "dist").mkdir()
                shutil.copyfile(package / "本地构建回执.json", staging / "build/local-exe-selftest-receipt.json")
                shutil.copyfile(exe, staging / "dist/Lazarus-Agent.exe")
                verify_workspace(staging, archive, files, build)
                staging.rename(workspace)
    return build


def main():
    parser = ArgumentParser(description=__doc__)
    for field in ("exe", "workspace"):
        parser.add_argument("--" + field, type=Path, required=True)
    for field in ("sdk", "java-home", "apktool"):
        parser.add_argument("--" + field, type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check-only", action="store_true")
    mode.add_argument("--audit", action="store_true")
    opts = parser.parse_args()
    if not (opts.check_only or opts.audit) and not all((opts.sdk, opts.java_home, opts.apktool)):
        parser.error("实跑需要明确指定已准备好的 --sdk、--java-home 和 --apktool")
    workspace = opts.workspace.resolve()
    build = prepare_workspace(ROOT.parent, workspace, opts.exe.resolve())
    frozen = read(ROOT / "plan.json")
    if frozen["build"] != build:
        raise ValueError("离线工作区与冻结计划版本不一致")
    sys.dont_write_bytecode = True
    sys.path.insert(1, str(workspace))
    for name in tuple(os.environ):
        if name.upper().startswith(("LAZARUS_", "REVIVER_")):
            del os.environ[name]
    output = workspace / "build/f01-recorded-cohort"
    os.environ.update(LAZARUS_STORAGE_ROOT=str(output / "storage"), LAZARUS_MIGRATE_LEGACY="0")
    if opts.audit:
        sys.path.insert(1, str(workspace / "tools"))
        from tools.audit_f01_recorded_cohort import audit

        print(json.dumps(audit(workspace, output, validated_build=build, expected_plan=frozen),
                         ensure_ascii=False, indent=2))
        return
    if not opts.check_only:
        sdk, java, apktool = (path.resolve() for path in (opts.sdk, opts.java_home, opts.apktool))
        image_root = sdk / "system-images" / f"android-{frozen['api']}"
        if not any((image_root / tag / "x86_64/system.img").is_file()
                   for tag in ("google_apis_playstore", "google_apis", "default")):
            raise ValueError("指定 SDK 缺少 Android 16 x86_64 系统镜像")
        os.environ.update(REVIVER_SDK=str(sdk), REVIVER_JAVA_HOME=str(java), REVIVER_APKTOOL_JAR=str(apktool))
        from apk_edit_build import apktool_jar
        import bench_device

        if (bench_device.SDK.resolve() != sdk or not bench_device.avd_ready(frozen["api"])
                or not all((java / "bin" / name).is_file() for name in ("java.exe", "keytool.exe"))):
            raise ValueError("指定的 Android 16 SDK 或 Java 运行环境不完整")
        apktool_jar()
    replay(workspace, None, output, check_only=opts.check_only, validated_build=build, expected_plan=frozen)


if __name__ == "__main__":
    main()
