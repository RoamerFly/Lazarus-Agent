"""Assemble an allowlisted F01 review package for the current Agent implementation."""
from argparse import ArgumentParser
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent_acceptance import verified_for
from app_version import APP_VERSION
from updater import parse_manifest, verify_manifest
from tools.run_f01_p1 import PROTOCOL
from tools.summarize_f01_p1 import summarize


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def copy(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def add_case(root, output, case, mode, repeat):
    trial = root / f"{case['id']}-{mode}-{repeat}"
    if not trial.is_dir():
        return
    target = output / "评测记录" / trial.name
    for name in ("started.json", "result.json", "acceptance.json", "usage.json", "changes.diff", "agent-trace.json"):
        source = trial / name
        if source.is_file():
            copy(source, target / name)
    proof = trial / "acceptance.json"
    candidate = trial / "candidate.apk"
    if proof.is_file() and candidate.is_file():
        report = read(proof)
        if digest(candidate) != report["candidate_sha256"]:
            raise ValueError("候选与验收报告哈希不符：" + trial.name)
        # Trial candidates retain their independent report, including failures.
        copy(candidate, target / "candidate.apk")
        if mode == "agent" and verified_for(report, digest(candidate)):
            sample = output / "可分发样本" / trial.name
            source = ROOT / case["apk"]
            if digest(source) != report["input_sha256"]:
                raise ValueError("输入样本与验收报告哈希不符：" + trial.name)
            copy(source, sample / "base.apk")
            copy(candidate, sample / "candidate.apk")
            copy(proof, sample / "agent-acceptance.json")


def download_script(manifest):
    asset = manifest.assets["windows-x64-installer"]
    if not re.fullmatch(r"[a-f0-9]{64}", asset.sha256):
        raise ValueError("发布包哈希无效")
    url = f"https://github.com/RoamerFly/Lazarus-Agent/releases/download/v{APP_VERSION}/Lazarus-Agent-windows-x64-setup.exe"
    if asset.url != url:
        raise ValueError("更新清单的发布地址与版本不一致")
    return f'''$ErrorActionPreference = 'Stop'
$target = Join-Path $PSScriptRoot 'Lazarus-Agent-windows-x64-setup.exe'
if (!(Test-Path -LiteralPath $target)) {{
    $partial = $target + '.partial'
    if (Test-Path -LiteralPath $partial) {{ throw '发现未完成下载，请先检查 .partial 文件' }}
    Invoke-WebRequest -Uri '{url}' -OutFile $partial
    if ((Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash.ToLowerInvariant() -ne '{asset.sha256}') {{
        throw '下载文件校验失败，请保留现场文件并重新核对发布版本'
    }}
    Move-Item -LiteralPath $partial -Destination $target
}}
if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -ne '{asset.sha256}') {{
    throw '本地安装包校验失败'
}}
Write-Output "校验通过。请打开 $target 安装 Lazarus Agent {APP_VERSION}。"
'''


def build(results, release_dir, output, capture_dir):
    results = results.resolve()
    if not results.is_relative_to(ROOT / "build") or not results.is_dir():
        raise ValueError("请指定 build 目录内的原始评测结果")
    status = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT).decode()
    if status.strip():
        raise ValueError("先提交当前源码，再生成对应提交的复核包")
    raw_manifest = (release_dir / "latest.json").read_bytes()
    signature = (release_dir / "latest.json.sig").read_bytes()
    verify_manifest(raw_manifest, signature, ROOT / "update/lazarus-update-public.pem")
    manifest = parse_manifest(raw_manifest)
    if manifest.version != APP_VERSION:
        raise ValueError("发布版本与当前程序版本不同")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()
    protocol = read(PROTOCOL)
    summary = summarize(results, protocol)
    proposal = ROOT / "交付/遗留软件复活智能体方案说明.pdf"
    video = ROOT / "交付/遗留软件复活智能体演示.mp4"
    metadata = read(video.with_suffix(".json"))
    if metadata["app_version"] != APP_VERSION or metadata["video_sha256"] != digest(video):
        raise ValueError("演示版本或视频哈希与当前材料不符")
    demo_proof = read(capture_dir / "agent-acceptance.json")
    demo_candidate = capture_dir / "candidate.apk"
    demo_source = ROOT / "fixtures/field-route/field-route-legacy.apk"
    if (not verified_for(demo_proof, digest(demo_candidate)) or
            digest(demo_source) != demo_proof["input_sha256"] or
            demo_proof["candidate_sha256"] != metadata["capture"]["candidate_sha256"]):
        raise ValueError("演示 APK、视频和独立验收没有对应到同一产物")
    with tempfile.TemporaryDirectory(dir=ROOT / "build", prefix="review-") as temporary:
        kit = Path(temporary)
        copy(proposal, kit / proposal.name)
        copy(video, kit / video.name)
        copy(video.with_suffix(".json"), kit / "演示记录.json")
        demo_sample = kit / "可分发样本/工作台演示"
        copy(demo_source, demo_sample / "base.apk")
        copy(demo_candidate, demo_sample / "candidate.apk")
        copy(capture_dir / "agent-acceptance.json", demo_sample / "agent-acceptance.json")
        demo_job = read(capture_dir / "job.json")
        demo_run = demo_job["latest_feature_candidate"]
        demo_trace = capture_dir / "storage/data/jobs" / demo_job["id"] / "feature-candidates" / demo_run
        for name in ("changes.diff", "agent-trace.json"):
            copy(demo_trace / name, kit / "评测记录/工作台演示" / name)
        for name in ("初赛复核说明.md", "运行指南.md", "fixtures/评审样本使用说明.md",
                     "docs/F01-参赛优化与验收计划.md", "docs/F01-试点与部署验收.md",
                     "docs/F01-答辩提纲.md", "docs/F01-交付验收清单.md"):
            copy(ROOT / name, kit / Path(name).name)
        for name in ("f01-independent-acceptance.md", "f01-p1-results.json", "f01-p1-review.md",
                     "0.6.7-independent-windows.md", "0.6.8-independent-windows.md",
                     "0.6.8-windows-proof.json", "0.6.8-live-demo.md"):
            path = ROOT / "docs/validation" / name
            if path.is_file():
                copy(path, kit / "验证摘要" / name)
        source = kit / "F01-源代码.zip"
        subprocess.run(["git", "archive", "--format=zip", "--output=" + str(source), "HEAD"],
                       cwd=ROOT, check=True)
        subprocess.run(["git", "archive", "--format=zip",
                        "--output=" + str(kit / "F01-固定评测源码-17388e9.zip"), "17388e9"],
                       cwd=ROOT, check=True)
        (kit / "源码版本.txt").write_text(commit + "\n", encoding="utf-8")
        desktop = kit / "Windows桌面版"
        desktop.mkdir()
        copy(release_dir / "latest.json", desktop / "latest.json")
        copy(release_dir / "latest.json.sig", desktop / "latest.json.sig")
        (desktop / "下载并校验安装包.ps1").write_text(download_script(manifest), encoding="utf-8-sig")
        for case in protocol["cases"]:
            source = ROOT / case["apk"]
            copy(source, kit / "自有输入样本" / source.name)
            modes = [("agent", n) for n in range(1, protocol[case["class"] + "_repetitions"] + 1)]
            if case["class"] == "positive":
                modes.extend((mode, 1) for mode in ("roundtrip", "rules", "one-build"))
            for mode, repeat in modes:
                add_case(results, kit, case, mode, repeat)
        # Only synthetic fixture evidence enters this package. Remove local workstation roots in text.
        for path in (kit / "评测记录").rglob("*"):
            if path.is_file() and path.suffix in {".json", ".diff"}:
                text = path.read_text(encoding="utf-8")
                for original in (str(results), str(ROOT), str(Path.home())):
                    text = text.replace(original.replace("\\", "\\\\"), "<LOCAL>")
                    text = text.replace(original, "<LOCAL>")
                path.write_text(text, encoding="utf-8")
        package_status = {"app_version": APP_VERSION, "source_commit": commit,
                          "purpose": "engineering-review", "ready_for_submission": False,
                          "benchmark": summary["counts"],
                          "pending": ["主案例当前版本重复性回归", "非开发人员试用记录",
                                      "提交前最终独立复核"]}
        (kit / "复核状态.json").write_text(json.dumps(package_status, ensure_ascii=False, indent=2), encoding="utf-8")
        files = sorted(path for path in kit.rglob("*") if path.is_file())
        (kit / "SHA256SUMS.txt").write_text(
            "".join(f"{digest(path)}  {path.relative_to(kit).as_posix()}\n" for path in files),
            encoding="utf-8")
        output.parent.mkdir(parents=True, exist_ok=True)
        staged = output.with_suffix(".partial")
        if staged.exists():
            raise ValueError("发现未完成的打包文件，请先核对")
        with ZipFile(staged, "x", ZIP_DEFLATED) as archive:
            for path in sorted(kit.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(kit).as_posix())
        with ZipFile(staged) as archive:
            if archive.testzip() is not None:
                raise ValueError("复核 ZIP 读取校验失败")
        if staged.stat().st_size > 100 * 1024 * 1024:
            raise ValueError("复核包超过 100 MiB，请拆分评审附件")
        staged.replace(output)
    print(output, output.stat().st_size, digest(output))


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--capture-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "交付/重邮GGBond-F01-工程复核包.zip")
    args = parser.parse_args()
    build(args.results, args.release_dir, args.output, args.capture_dir)


if __name__ == "__main__":
    main()
