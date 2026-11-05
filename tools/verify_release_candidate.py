"""Bind an existing signed Windows build to its independent validation receipts."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from updater import parse_manifest, verify_manifest

NAMES = {
    "windows-x64-installer": "Lazarus-Agent-windows-x64-setup.exe",
    "windows-x64-portable-exe": "Lazarus-Agent-windows-x64-portable.exe",
}


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify(directory, receipt, version, build_run, commit):
    raw = (directory / "latest.json").read_bytes()
    verify_manifest(raw, (directory / "latest.json.sig").read_bytes())
    manifest = parse_manifest(raw)
    installer = receipt["installer"]
    if (manifest.version != version or receipt["version"] != version or receipt["source_build_run"] != build_run
            or receipt["source_commit"] != commit or receipt["manifest_sha256"] != digest(directory / "latest.json")
            or not installer["signature_verified"] or installer["tampered_exit"] != 2
            or not installer["user_output_preserved"]
            or any(installer[key] != 0 for key in ("install_exit", "self_test_exit", "input_check_exit", "uninstall_exit"))
            or installer["installer_sha256"] != manifest.assets["windows-x64-installer"].sha256
            or installer["installed_exe_sha256"] != manifest.assets["windows-x64-portable-exe"].sha256):
        raise ValueError("签名资产或独立安装回执与候选构建不符")
    runtime = receipt["runtime"]
    if runtime["replay"]["behavior_steps"] != 10 or runtime["offline_repair"]["model_enabled"] is not False:
        raise ValueError("缺少两种干净 Windows 运行验证")
    for key, name in NAMES.items():
        if digest(directory / name) != manifest.assets[key].sha256:
            raise ValueError(f"签名清单中的文件哈希不符 {name}")
    portable = directory / "Lazarus-Agent-windows-x64-portable.zip"
    with ZipFile(portable) as archive:
        if sorted(archive.namelist()) != ["Lazarus-Agent.exe", "portable.flag"]:
            raise ValueError("便携包文件列表不符")
        with archive.open("Lazarus-Agent.exe") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != installer["installed_exe_sha256"]:
                raise ValueError("便携包 EXE 与独立安装回执不符")
    files = [*NAMES.values(), portable.name, "latest.json", "latest.json.sig"]
    return {name: {"sha256": digest(directory / name), "bytes": (directory / name).stat().st_size}
            for name in files}


def require_p1c():
    validation = ROOT / "docs/validation/f01-p1c"
    result_path, review_path = validation / "results.json", validation / "diff-review.json"
    if not result_path.is_file() or not review_path.is_file():
        raise ValueError("P1C 评测结果与差异审查尚未归档")
    result = read(result_path)
    counts = result["counts"]
    if not (result["planned_trials"] == result["terminal_trials"] == 42 and result["joint_gate"]
            and counts["agent_positives"] >= 15 and counts["primary"] == 3
            and counts["agent_negative_rejected"] == 6 and counts["agent_false_acceptances"] == 0
            and result["held_out_passes"] >= 2):
        raise ValueError("P1C 冻结评测尚未达到完整发布门槛")
    reviewed = read(review_path)["rows"]
    expected = {(row["trial"], row["candidate_sha256"], row["raw_diff_sha256"]) for row in result["rows"]
                if row["state"] == "passed"}
    checked = {(row["trial"], row["candidate_sha256"], row["raw_diff_sha256"]) for row in reviewed
               if row["review"].strip() and row["scope"].strip()}
    if checked != expected or len(reviewed) != len(expected):
        raise ValueError("P1C 通过候选的差异审查尚未完成")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--build-run", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--require-p1c", action="store_true")
    args = parser.parse_args()
    if args.require_p1c:
        require_p1c()
    receipt = read(ROOT / f"docs/validation/{args.version}-candidate-windows-runtime.json")
    print(json.dumps(verify(args.directory, receipt, args.version, args.build_run, args.source_commit),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
