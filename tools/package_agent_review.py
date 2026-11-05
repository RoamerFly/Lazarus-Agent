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
from tools.audit_f01_p1b import SPEC as P1B_PROTOCOL, audit as audit_p1b, require_diff_review
from tools.run_f01_p1 import PROTOCOL, trial_schedule
from tools.summarize_f01_p1 import summarize


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def copy(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def add_case(root, output, case, mode, repeat, *, distribute=True, sample_root=None):
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
        if distribute and mode == "agent" and verified_for(report, digest(candidate)):
            sample = (sample_root or output / "可分发样本") / trial.name
            source = ROOT / case["apk"]
            if digest(source) != report["input_sha256"]:
                raise ValueError("输入样本与验收报告哈希不符：" + trial.name)
            copy(source, sample / "base.apk")
            copy(candidate, sample / "candidate.apk")
            copy(proof, sample / "agent-acceptance.json")


def add_counterexample(results, output, case):
    """Export a fully observed contradictory-plan rejection, separately from passes."""
    if case["class"] != "negative":
        return
    trial = results / (case["id"] + "-agent-1")
    proof, candidate = trial / "acceptance.json", trial / "candidate.apk"
    if not proof.is_file() or not candidate.is_file():
        return
    report = read(proof)
    steps = report.get("steps", [])
    if not (len(steps) == 2 and steps[0]["action"] == "expect" and steps[1]["action"] == "absent"
            and steps[0]["value"] == steps[1]["value"] and report["status"] == "incomplete"):
        return
    observed = report["rows"][0]["candidate"]
    checks = observed.get("steps", [])
    if not (observed["install"] == observed["launch"] == "passed" and observed["function"] == "failed"
            and len(checks) == 2 and [s["status"] for s in checks] == ["passed", "failed"]):
        raise ValueError("矛盾断言反例缺少预期的安装、启动或行为失败证据")
    source = ROOT / case["apk"]
    if digest(source) != report["input_sha256"] or digest(candidate) != report["candidate_sha256"]:
        raise ValueError("反例 APK 与报告哈希不符")
    sample = output / "反例样本" / trial.name
    for original, name in ((source, "base.apk"), (candidate, "candidate.apk"), (proof, "agent-acceptance.json")):
        copy(original, sample / name)
    expectation = {"exit_code": 1, "steps": [
        {key: check[key] for key in ("action", "value", "status", "match_count")} for check in checks]}
    (sample / "expected-outcome.json").write_text(json.dumps(expectation, ensure_ascii=False, indent=2), encoding="utf-8")


FILE_CHECK_SCRIPT = r'''$ErrorActionPreference = 'Stop'
Import-Module Microsoft.PowerShell.Utility -ErrorAction Stop
$root = [IO.Path]::GetFullPath($PSScriptRoot)
$count = 0
foreach ($line in Get-Content -LiteralPath (Join-Path $root 'SHA256SUMS.txt') -Encoding utf8) {
    if ($line -notmatch '^([a-f0-9]{64})  (.+)$') { throw '文件校验清单格式无效' }
    $expected = $Matches[1]
    $relative = $Matches[2]
    $path = [IO.Path]::GetFullPath((Join-Path $root $relative))
    if (!$path.StartsWith($root + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw '文件校验路径超出复核包目录'
    }
    if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) {
        throw "文件校验失败：$relative"
    }
    $count++
}
Write-Output "已核对 $count 个登记文件的 SHA-256。应用功能请继续按指南复测。"
'''


def download_script(manifest):
    asset = manifest.assets["windows-x64-installer"]
    if not re.fullmatch(r"[a-f0-9]{64}", asset.sha256):
        raise ValueError("发布包哈希无效")
    url = f"https://github.com/RoamerFly/Lazarus-Agent/releases/download/v{APP_VERSION}/Lazarus-Agent-windows-x64-setup.exe"
    if asset.url != url:
        raise ValueError("更新清单的发布地址与版本不一致")
    return f'''$ErrorActionPreference = 'Stop'
Import-Module Microsoft.PowerShell.Utility -ErrorAction Stop
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


def build(results, release_dir, output, capture_dir, regression_results=None, regression_summary=None,
          primary_results=None, primary_summary=None, materials_dir=ROOT / "交付", p1b_checkout=None):
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
    p1b = None
    if p1b_checkout is not None:
        p1b_checkout = p1b_checkout.resolve()
        if not p1b_checkout.is_relative_to(ROOT / "build"):
            raise ValueError("请指定 build 目录内的 P1B 冻结检出")
        p1b = audit_p1b(p1b_checkout)
        if p1b != read(ROOT / "docs/validation/f01-p1b/results.json"):
            raise ValueError("P1B 汇总与冻结记录不一致")
        require_diff_review(p1b, read(ROOT / "docs/validation/f01-p1b/diff-review.json"))
    primary = None
    primary_version = None
    if primary_results is not None:
        primary_results = primary_results.resolve()
        if not primary_results.is_relative_to(ROOT / "build") or not primary_results.is_dir():
            raise ValueError("请指定 build 目录内的发版主案例回归结果")
        primary_cases = [case for case in protocol["cases"] if case["id"] == protocol["primary"]]
        primary = summarize(primary_results, {**protocol, "cases": primary_cases})
        recorded = read(primary_summary or ROOT / "docs/validation" / f"{APP_VERSION}-primary-regression.json")
        primary_version = recorded["app_version"]
        version_source = subprocess.check_output(
            ["git", "show", recorded["run_worktree_commit"] + ":app_version.py"], cwd=ROOT).decode("utf-8")
        if (not re.fullmatch(r"\d+\.\d+\.\d+", primary_version)
                or f'APP_VERSION = "{primary_version}"' not in version_source
                or recorded["fixed_protocol_sha256"] != digest(PROTOCOL)
                or recorded["agent_passes"] != primary["counts"]["primary"]):
            raise ValueError("发版主案例回归摘要与原始评测不符")
        recorded_rows = {row["trial"]: row for row in recorded["rows"]}
        for row in primary["rows"]:
            trial = f"{row['case']}-{row['mode']}-{row['repeat']}"
            if not (primary_results / trial / "result.json").is_file():
                raise ValueError("发版主案例回归轮次尚未形成终态：" + trial)
            artifact = primary_results / trial / "candidate.apk"
            sha256 = digest(artifact) if artifact.is_file() else None
            note = recorded_rows.get(trial, {})
            if note.get("state") != row["state"] or note.get("candidate_sha256") != sha256:
                raise ValueError("发版主案例回归轮次的结果或候选哈希不符：" + trial)
    regression = None
    if regression_results is not None:
        regression_results = regression_results.resolve()
        if not regression_results.is_relative_to(ROOT / "build") or not regression_results.is_dir():
            raise ValueError("请指定 build 目录内的当前版本回归结果")
        regression = summarize(regression_results, protocol)
    regression_metadata = None
    if regression_summary is not None:
        recorded = read(regression_summary)
        if regression is None or any(recorded[key] != regression[key] for key in ("counts", "rows")):
            raise ValueError("回归摘要与本次原始记录不符")
        implementation = recorded["implementation_commit"]
        version_source = subprocess.check_output(
            ["git", "show", implementation + ":app_version.py"], cwd=ROOT).decode("utf-8")
        if f'APP_VERSION = "{recorded["app_version"]}"' not in version_source:
            raise ValueError("回归摘要的版本与实施提交不符")
        regression_metadata = {"app_version": recorded["app_version"], "source_commit": implementation,
                               "role": recorded["evaluation_role"]}
    proposal = materials_dir / "遗留软件复活智能体方案说明.pdf"
    video = materials_dir / "遗留软件复活智能体演示.mp4"
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
        copy(ROOT / "桌面复核运行指南.md", kit / "运行指南.md")
        for name in ("初赛复核说明.md", "fixtures/评审样本使用说明.md",
                     "docs/F01-参赛优化与验收计划.md", "docs/F01-试点与部署验收.md",
                     "docs/F01-答辩提纲.md", "docs/F01-交付验收清单.md",
                     "docs/science-superpowers/preregistrations/2026-11-05-f01-apk-agent.md",
                     "docs/science-superpowers/preregistrations/2026-11-06-f01-p1b.md"):
            copy(ROOT / name, kit / name)
        window_evidence = Path("docs/validation") / f"{APP_VERSION}-local-rule-window"
        for name in ("before-system-ui-wait.png", "before-system-ui-wait.xml", "after.png", "after.xml"):
            path = window_evidence / name
            if (ROOT / path).is_file():
                copy(ROOT / path, kit / path)
        for name in ("f01-independent-acceptance.md", "f01-p1-results.json", "f01-p1-review.md",
                     "0.6.6-general-agent.md",
                     "0.6.7-independent-windows.md", "0.6.8-independent-windows.md",
                     "0.6.8-windows-proof.json", "0.6.8-live-demo.md", "0.6.8-primary-regression.json",
                     "0.6.8-regression-results.json", "0.6.8-regression-review.md",
                     "f01-package-replay.md", "0.6.9-windows-proof.json",
                     "0.6.9-independent-windows.md", "0.6.9-live-demo.md",
                     "0.6.9-first-use-failure.md", "0.6.9-first-use-failure.json",
                     "0.6.10-windows-proof.json", "0.6.10-independent-windows.md",
                     "0.6.10-live-demo.md", "0.6.10-installer-check.md", "0.6.10-installer-check.json",
                     "0.6.10-counterexample-replay.md", "0.6.10-counterexample-replay.json",
                     "0.6.10-package-replay.json", "0.6.10-cold-start-observation.md",
                     "0.6.10-cold-start-observation.json", "0.6.10-local-rule-windows.md",
                     "0.6.10-local-rule-windows.json", "0.6.10-primary-regression.md",
                     "0.6.10-primary-regression.json", "0.6.10-first-import-no-path.md",
                     "0.6.11-live-demo.md", "0.6.11-windows-proof.json", "0.6.11-independent-windows.md",
                     "0.6.11-installer-check.json", "0.6.11-installer-check.md",
                     "0.6.12-live-demo.md", "0.6.12-windows-proof.json", "0.6.12-independent-windows.md",
                     "0.6.12-installer-check.json", "0.6.12-installer-check.md", "0.6.12-tool-paths-local.md",
                     "0.6.13-local-rule-observation.md", "0.6.13-local-rule-observation.json",
                     "0.6.13-independent-windows.md", "0.6.13-windows-proof.json",
                     "0.6.13-installer-check.md", "0.6.13-installer-check.json", "0.6.13-package-replay.md",
                     "0.6.14-cold-start-observation.md", "0.6.14-cold-start-observation.json",
                     "0.6.14-local-rule-windows.md", "0.6.14-local-rule-windows.json", "0.6.14-tool-paths-local.md",
                     f"{APP_VERSION}-live-demo.md", f"{APP_VERSION}-windows-proof.json",
                     f"{APP_VERSION}-independent-windows.md", f"{APP_VERSION}-installer-check.json",
                     f"{APP_VERSION}-installer-check.md", f"{APP_VERSION}-local-rule-windows.json",
                     f"{APP_VERSION}-local-rule-windows.md", f"{APP_VERSION}-tool-paths-local.md",
                     f"{APP_VERSION}-local-rule-observation.md", f"{APP_VERSION}-local-rule-observation.json",
                     f"{APP_VERSION}-cold-start-observation.md", f"{APP_VERSION}-cold-start-observation.json",
                     f"{APP_VERSION}-source-replay.md", f"{APP_VERSION}-source-replay.json"):
            path = ROOT / "docs/validation" / name
            if path.is_file():
                copy(path, kit / "docs/validation" / name)
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
        (kit / "复测样本.ps1").write_text(
            (ROOT / "tools/review_replay.ps1").read_text(encoding="utf-8-sig"), encoding="utf-8-sig")
        for case in protocol["cases"]:
            source = ROOT / case["apk"]
            copy(source, kit / "自有输入样本" / source.name)
            add_counterexample(results, kit, case)
            modes = [("agent", n) for n in range(1, protocol[case["class"] + "_repetitions"] + 1)]
            if case["class"] == "positive":
                modes.extend((mode, 1) for mode in ("roundtrip", "rules", "one-build"))
            for mode, repeat in modes:
                add_case(results, kit, case, mode, repeat)
                if regression is not None:
                    label = (f"v{regression_metadata['app_version']} 全组开发回归"
                             if regression_metadata else "全组开发回归")
                    add_case(regression_results, kit / label, case, mode, repeat)
                if primary is not None and case["id"] == protocol["primary"]:
                    add_case(primary_results, kit / f"v{primary_version} 主案例开发回归", case, mode, repeat)
        if p1b is not None:
            p1b_folder = kit / "P1B 前瞻复验"
            p1b_protocol = read(p1b_checkout / P1B_PROTOCOL)
            copy(p1b_checkout / P1B_PROTOCOL, p1b_folder / P1B_PROTOCOL.name)
            freeze = p1b["freeze_commit"]
            subprocess.run(["git", "archive", "--format=zip",
                            "--output=" + str(p1b_folder / f"F01-P1B-冻结源码-{freeze[:7]}.zip"), freeze],
                           cwd=ROOT, check=True)
            for name in ("results.json", "report.md", "diff-review.json"):
                path = Path("docs/validation/f01-p1b") / name
                copy(ROOT / path, kit / path)
            for case in p1b_protocol["cases"]:
                source = ROOT / case["apk"]
                copy(source, p1b_folder / "自有输入样本" / source.name)
            for (case, mode, repeat), row in zip(trial_schedule(p1b_protocol), p1b["rows"]):
                add_case(p1b_checkout / "build/p1b", p1b_folder, case, mode, repeat,
                         distribute=row["state"] == "passed", sample_root=kit / "可分发样本/P1B")
        # Only synthetic fixture evidence enters this package. Remove local workstation roots in text.
        for path in kit.rglob("*"):
            if "评测记录" not in path.parts:
                continue
            if path.is_file() and path.suffix in {".json", ".diff"}:
                text = path.read_text(encoding="utf-8")
                for original in (str(results), str(p1b_checkout or ROOT), str(ROOT), str(Path.home())):
                    text = text.replace(original.replace("\\", "\\\\"), "<LOCAL>")
                    text = text.replace(original, "<LOCAL>")
                path.write_text(text, encoding="utf-8")
        package_status = {"app_version": APP_VERSION, "source_commit": commit,
                          "purpose": "engineering-review", "ready_for_submission": False,
                          "frozen_benchmark_source": "17388e9", "benchmark": summary["counts"],
                          "current_regression": regression["counts"] if regression else None,
                          "regression_implementation": regression_metadata,
                          "release_primary_regression": primary["counts"] if primary else None,
                          "primary_regression_version": primary_version,
                          "pending": (["当前版本全组回归与差异审查"] if regression is None else []) +
                                     ["非开发人员试用记录", "负责人提交前核对赛事平台要求"]}
        counts = summary["counts"]
        package_status["original_frozen_gate"] = (
            counts["agent_positives"] >= 15 and counts["primary"] == 3 and
            counts["agent_negative_rejected"] == 6 and counts["agent_false_acceptances"] == 0
        )
        if p1b is None:
            package_status["pending"].append("P1B 前瞻复验结果待归档")
        else:
            package_status["prospective_replication"] = {
                key: p1b[key] for key in ("freeze_commit", "protocol_sha256", "planned_trials", "terminal_trials",
                                           "held_out_passes", "joint_gate", "counts")
            }
            if not p1b["joint_gate"]:
                package_status["pending"].append("P1B 前瞻复验联合门槛未达成")
        if not (ROOT / "docs/validation" / f"{APP_VERSION}-installer-check.json").is_file():
            package_status["pending"].append("正式版安装器的独立核验")
        if not (ROOT / "docs/validation" / f"{APP_VERSION}-local-rule-windows.json").is_file():
            package_status["pending"].append("正式版在干净 Windows 上关闭模型并生成新 APK 的复验")
        windows_proof = ROOT / "docs/validation" / f"{APP_VERSION}-windows-proof.json"
        if windows_proof.is_file():
            record = read(windows_proof)
            package_status["release_source_commit"] = record["release_source_commit"]
            package_status["independent_windows_workflow"] = record["workflow"]
        else:
            package_status["pending"].append("正式版在干净 Windows 上首次准备及候选复测")
        (kit / "复核状态.json").write_text(json.dumps(package_status, ensure_ascii=False, indent=2), encoding="utf-8")
        (kit / "校验文件.ps1").write_text(FILE_CHECK_SCRIPT, encoding="utf-8-sig")
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
    parser.add_argument("--regression-results", type=Path)
    parser.add_argument("--regression-summary", type=Path)
    parser.add_argument("--primary-results", type=Path)
    parser.add_argument("--primary-summary", type=Path)
    parser.add_argument("--p1b-checkout", type=Path, help="Frozen checkout with completed P1B evidence")
    parser.add_argument("--materials-dir", type=Path, default=ROOT / "交付", help="方案 PDF、演示视频和捕获记录的目录")
    parser.add_argument("--output", type=Path, default=ROOT / "交付/重邮GGBond-F01-工程复核包.zip")
    args = parser.parse_args()
    build(args.results, args.release_dir, args.output, args.capture_dir,
          args.regression_results, args.regression_summary, args.primary_results, args.primary_summary, args.materials_dir,
          args.p1b_checkout)


if __name__ == "__main__":
    main()
