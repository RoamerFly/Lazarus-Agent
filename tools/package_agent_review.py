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
from tools.audit_f01_p1b import P1B, p1c_batch, require_diff_review
from tools.audit_f01_release_regression import audit_release_regression
from tools.run_f01_p1 import PROTOCOL, trial_schedule
from tools.summarize_f01_p1 import summarize
from tools.verify_release_candidate import require_p1c
from tools.verify_release_candidate import verify as verify_signed_candidate
from tools.verify_review_package import verify as verify_review_archive

HISTORICAL_AUDITOR = "41fe5b1af3f047dbee730d672e4a5639fe61aa59"


def audit_frozen(checkout, label):
    # Score historical proofs using the acceptance code shipped with those audits.
    with tempfile.TemporaryDirectory(dir=ROOT / "build") as directory:
        workspace = Path(directory)
        source = workspace / "source"
        archive_path = workspace / "auditor.zip"
        subprocess.run(["git", "archive", "--format=zip", f"--output={archive_path}", HISTORICAL_AUDITOR],
                       cwd=ROOT, check=True)
        with ZipFile(archive_path) as archive:
            archive.extractall(source)
        output = subprocess.check_output([sys.executable, "-X", "utf8", str(source / "tools/audit_f01_p1b.py"),
                                          str(checkout), "--batch", label.lower()], cwd=source)
    return json.loads(output)


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
Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Utility/Microsoft.PowerShell.Utility.psd1') -ErrorAction Stop
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
Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Utility/Microsoft.PowerShell.Utility.psd1') -ErrorAction Stop
$target = Join-Path $PSScriptRoot 'Lazarus-Agent-windows-x64-setup.exe'
if (!(Test-Path -LiteralPath $target -PathType Leaf)) {{
    $partial = $target + '.' + [Guid]::NewGuid().ToString('N') + '.partial'
    try {{
        Write-Output '正在下载固定版本的 Windows 安装包。'
        Invoke-WebRequest -UseBasicParsing -Uri '{url}' -OutFile $partial
        if ((Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash.ToLowerInvariant() -ne '{asset.sha256}') {{
            throw '下载文件的 SHA-256 与发布记录不符'
        }}
        try {{
            [IO.File]::Move($partial, $target)
        }} catch [IO.IOException] {{
            if (!(Test-Path -LiteralPath $target -PathType Leaf)) {{ throw }}
        }}
    }} catch {{
        throw "安装包下载或校验失败。可从固定发布地址手动下载到此目录后重跑脚本。{url}`n$($_.Exception.Message)"
    }} finally {{
        if (Test-Path -LiteralPath $partial -PathType Leaf) {{
            Remove-Item -LiteralPath $partial -ErrorAction SilentlyContinue
        }}
    }}
}}
if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -ne '{asset.sha256}') {{
    throw '已有安装包的 SHA-256 与固定版本不符。请移出该文件，再重新运行校验脚本。'
}}
Write-Output "校验通过。请打开 $target 安装 Lazarus Agent {APP_VERSION}。"
'''


def build(results, release_dir, output, capture_dir, regression_results=None, regression_summary=None,
          primary_results=None, primary_summary=None, materials_dir=ROOT / "交付", p1b_checkout=None,
          p1c_checkout=None, release_regression=None, focused_results=None):
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
    publication_path = ROOT / "docs/validation" / f"{APP_VERSION}-release-publication.json"
    publication = read(publication_path) if publication_path.is_file() else None
    if publication:
        windows = read(publication_path.with_name(f"{APP_VERSION}-candidate-windows-runtime.json"))
        checked_assets = verify_signed_candidate(release_dir, windows, APP_VERSION, publication["signed_build_run"],
                                                 publication["source_commit"])
        public_assets = {name: {key: asset[key] for key in ("sha256", "bytes")}
                         for name, asset in publication["assets"].items()}
        if (publication["version"] != APP_VERSION or checked_assets != public_assets or
                publication["status"] != "public-release-assets-match-tested-signed-build" or
                not publication["published_at"]):
            raise ValueError("正式发布与已验证的签名构建不一致")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()
    protocol = read(PROTOCOL)
    summary = summarize(results, protocol)
    frozen_batches = {}
    for label, checkout in (("P1B", p1b_checkout), ("P1C", p1c_checkout)):
        if checkout is None:
            continue
        checkout = checkout.resolve()
        if not checkout.is_relative_to(ROOT / "build"):
            raise ValueError(f"请指定 build 目录内的 {label} 冻结检出")
        batch = P1B if label == "P1B" else p1c_batch(checkout)
        audit_result = audit_frozen(checkout, label)
        reports = Path("docs/validation") / f"f01-{label.lower()}"
        if audit_result != read(ROOT / reports / "results.json"):
            raise ValueError(f"{label} 汇总与冻结记录不一致")
        require_diff_review(audit_result, read(ROOT / reports / "diff-review.json"))
        frozen_batches[label] = (checkout, batch, audit_result, reports)
    current = None
    if release_regression:
        regression_checkout, raw_results, run_plan = (path.resolve() for path in release_regression)
        if not regression_checkout.is_relative_to(ROOT / "build"):
            raise ValueError("请指定 build 目录内的发布回归检出")
        current_reports = ROOT / "docs/validation/f01-release-regression" / APP_VERSION
        current = audit_release_regression(regression_checkout, raw_results, read(run_plan))
        if current["app_version"] != APP_VERSION or current != read(current_reports / "results.json"):
            raise ValueError("发布回归与当前版本的归档结果不一致")
        current_review = read(current_reports / "diff-review.json")
        require_diff_review(current, current_review)
        distributable = {row["trial"] for row in current_review["rows"] if row.get("distributable") is True}
    if publication and tuple(map(int, APP_VERSION.split("."))) >= (0, 6, 17):
        require_p1c(APP_VERSION, publication["signed_build_run"], publication["source_commit"])
        if current is None:
            raise ValueError("正式复核包缺少当前版本的发布回归原始记录")
    focused_samples = []
    if focused_results:
        focused_results = focused_results.resolve()
        if not focused_results.is_relative_to(ROOT / "build") or current is None:
            raise ValueError("专项样本需要当前版本发布回归和 build 目录内的原始记录")
        focused = read(ROOT / f"docs/validation/{APP_VERSION}-focused.json")
        if any(focused[key] != current[key] for key in ("app_version", "implementation_commit", "protocol_sha256")):
            raise ValueError("专项复测与签名构建的版本或协议不符")
        cases = {case["id"]: case for case in read(regression_checkout / p1c_batch(regression_checkout).spec)["cases"]}
        for row in focused["rows"]:
            if row.get("distributable") is not True:
                continue
            case, mode, repeat = cases[row["case"]], row["mode"], row["repeat"]
            trial = focused_results / f"{case['id']}-{mode}-{repeat}"
            sha = digest(trial / "candidate.apk")
            if (mode != "agent" or row["state"] != "passed" or sha != row["candidate_sha256"]
                    or digest(trial / "changes.diff") != row["raw_diff_sha256"]
                    or not verified_for(read(trial / "acceptance.json"), sha)):
                raise ValueError("专项样本与候选复核记录不符：" + trial.name)
            focused_samples.append((case, mode, repeat))
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
        device_screen = metadata["capture"].get("device_screen")
        if device_screen:
            screenshot = capture_dir / "device-final.png"
            if (device_screen["file"] != screenshot.name or device_screen["api"] != 36 or
                    device_screen["apk_sha256"] != demo_proof["candidate_sha256"] or
                    device_screen["sha256"] != digest(screenshot)):
                raise ValueError("演示设备画面没有对应到验收候选")
            copy(screenshot, kit / "演示Android画面.png")
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
        if publication:
            copy(publication_path, kit / publication_path.relative_to(ROOT))
        for version in dict.fromkeys(("0.6.15", APP_VERSION)):
            window_evidence = Path("docs/validation") / f"{version}-local-rule-window"
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
                     "0.6.15-independent-windows.md", "0.6.15-windows-proof.json",
                     "0.6.15-installer-check.md", "0.6.15-installer-check.json",
                     "0.6.15-local-rule-windows.md", "0.6.15-local-rule-windows.json",
                     "0.6.16-counter20-feedback.md", "0.6.16-counter20-feedback.json",
                     "0.6.16-candidate-installer-check.md", "0.6.16-candidate-installer-check.json",
                     "0.6.16-candidate-windows-runtime.md", "0.6.16-candidate-windows-runtime.json",
                     "0.6.16-release-publication.json", "0.6.16-live-demo.md",
                     "0.6.16-r2-fresh-windows.md", "0.6.16-r2-fresh-windows.json", "0.6.16-r2-handoff-inputs.json",
                     "0.6.17-candidate-windows-runtime.md", "0.6.17-candidate-windows-runtime.json",
                     "0.6.17-candidate-publication-check.json",
                     "0.6.18-intent-boundary.md", "0.6.18-intent-boundary.json",
                     f"{APP_VERSION}-live-demo.md", f"{APP_VERSION}-windows-proof.json",
                     f"{APP_VERSION}-independent-windows.md", f"{APP_VERSION}-installer-check.json",
                     f"{APP_VERSION}-installer-check.md", f"{APP_VERSION}-local-rule-windows.json",
                     f"{APP_VERSION}-local-rule-windows.md", f"{APP_VERSION}-tool-paths-local.md",
                     f"{APP_VERSION}-local-rule-observation.md", f"{APP_VERSION}-local-rule-observation.json",
                     f"{APP_VERSION}-cold-start-observation.md", f"{APP_VERSION}-cold-start-observation.json",
                     f"{APP_VERSION}-candidate-installer-check.md", f"{APP_VERSION}-candidate-installer-check.json",
                     f"{APP_VERSION}-candidate-windows-runtime.md", f"{APP_VERSION}-candidate-windows-runtime.json",
                     f"{APP_VERSION}-candidate-publication-check.json", f"{APP_VERSION}-candidate-replay-inputs.json",
                     f"{APP_VERSION}-replace-input.md", f"{APP_VERSION}-replace-input.json",
                     f"{APP_VERSION}-focused.md", f"{APP_VERSION}-focused.json",
                     f"{APP_VERSION}-receiver-guard-preflight.md", f"{APP_VERSION}-receiver-guard-preflight.json",
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
        for label, (checkout, batch, audited, reports) in frozen_batches.items():
            batch_folder = kit / f"{label} 前瞻复验"
            batch_protocol = read(checkout / batch.spec)
            copy(checkout / batch.spec, batch_folder / batch.spec.name)
            copy(ROOT / batch.registration, kit / batch.registration)
            freeze = audited["freeze_commit"]
            subprocess.run(["git", "archive", "--format=zip",
                            "--output=" + str(batch_folder / f"F01-{label}-冻结源码-{freeze[:7]}.zip"), freeze],
                           cwd=ROOT, check=True)
            names = ["results.json", "report.md", "diff-review.json"]
            if label == "P1B":
                names.append("p01-runtime-observation.json")
            if label == "P1C":
                names.extend(("selected-signatures.json", "supplemental-acceptance.json", "supplemental-receipt.json"))
                for name in ("agent-rehearsal-device.md", "agent-rehearsal-device.json",
                             "agent-platform-library.md", "agent-platform-library-device.json",
                             "agent-platform-library-model.json", "f01-p1c-input-feasibility.json",
                             "f01-p1c-supplemental-plan.json"):
                    path = Path("docs/validation") / name
                    copy(ROOT / path, kit / path)
            for name in names:
                path = reports / name
                copy(ROOT / path, kit / path)
            for case in batch_protocol["cases"]:
                source = checkout / case["apk"]
                copy(source, batch_folder / "自有输入样本" / source.name)
            for (case, mode, repeat), row in zip(trial_schedule(batch_protocol), audited["rows"]):
                add_case(checkout / batch.results, batch_folder, case, mode, repeat,
                         distribute=row["state"] == "passed", sample_root=kit / "可分发样本" / label)
        if current:
            current_folder = kit / f"v{APP_VERSION} 发布回归"
            current_spec = regression_checkout / p1c_batch(regression_checkout).spec
            current_protocol = read(current_spec)
            copy(current_spec, current_folder / current_spec.name)
            copy(run_plan, current_folder / "plan.json")
            for version in dict.fromkeys(("0.6.17", APP_VERSION)):
                folder = current_reports.with_name(version)
                for name in ("results.json", "diff-review.json", "report.md"):
                    path = folder / name
                    if path.is_file():
                        copy(path, kit / path.relative_to(ROOT))
            for case in current_protocol["cases"]:
                sample = regression_checkout / case["apk"]
                copy(sample, current_folder / "自有输入样本" / sample.name)
            for (case, mode, repeat), row in zip(trial_schedule(current_protocol), current["rows"]):
                add_case(raw_results, current_folder, case, mode, repeat, distribute=row["trial"] in distributable,
                         sample_root=kit / "可分发样本" / f"发布回归-v{APP_VERSION}")
        for case, mode, repeat in focused_samples:
            add_case(focused_results, kit / f"v{APP_VERSION} 专项复测", case, mode, repeat,
                     sample_root=kit / "可分发样本" / f"专项复测-v{APP_VERSION}")
        # Only synthetic fixture evidence enters this package. Remove local workstation roots in text.
        common_git = Path(subprocess.check_output(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                                  cwd=ROOT, encoding="utf-8").strip())
        roots = (results, *(batch[0] for batch in frozen_batches.values()), ROOT, common_git.parent, Path.home())
        if current:
            roots += (regression_checkout,)
        if focused_results:
            roots += (focused_results,)
        private_roots = sorted({form for path in roots for form in (str(path), path.as_posix())},
                               key=len, reverse=True)
        for path in kit.rglob("*"):
            if "评测记录" not in path.parts:
                continue
            if path.is_file() and path.suffix in {".json", ".diff"}:
                text = path.read_text(encoding="utf-8")
                for original in private_roots:
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
                          "pending": (["当前版本全组回归与差异审查"] if current is None and
                                      (regression_metadata or {}).get("app_version") != APP_VERSION else []) +
                                     ["非开发人员试用记录", "负责人提交前核对赛事平台要求"]}
        if current:
            package_status["release_regression"] = {
                key: current[key] for key in ("app_version", "implementation_commit", "source_build_run",
                                             "evaluation_role", "freeze_commit", "protocol_sha256",
                                             "planned_trials", "terminal_trials", "joint_gate", "counts")
            }
            package_status["release_regression"]["excluded_from_distribution"] = sorted(
                row["trial"] for row in current_review["rows"] if row["trial"] not in distributable
            )
            if not current["joint_gate"]:
                package_status["pending"].append("当前版本发布回归联合门槛未达成")
        counts = summary["counts"]
        package_status["original_frozen_gate"] = (
            counts["agent_positives"] >= 15 and counts["primary"] == 3 and
            counts["agent_negative_rejected"] == 6 and counts["agent_false_acceptances"] == 0
        )
        if "P1B" not in frozen_batches:
            package_status["pending"].append("P1B 前瞻复验结果待归档")
        for label, (_, _, audited, _) in frozen_batches.items():
            field = "prospective_replication" if label == "P1B" else "p1c_prospective_replication"
            package_status[field] = {
                key: audited[key] for key in ("freeze_commit", "protocol_sha256", "planned_trials", "terminal_trials",
                                             "held_out_passes", "joint_gate", "counts")
            }
            if not audited["joint_gate"] and (label == "P1C" or "P1C" not in frozen_batches):
                package_status["pending"].append(f"{label} 前瞻复验联合门槛未达成")
        if publication:
            package_status["release_source_commit"] = publication["source_commit"]
            package_status["release_url"] = publication["release_url"]
            package_status["independent_windows_workflows"] = publication["independent_workflow_runs"]
        else:
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
        verify_review_archive(staged)
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
    parser.add_argument("--p1c-checkout", type=Path, help="Frozen checkout with completed P1C evidence")
    parser.add_argument("--release-regression", type=Path, nargs=3, metavar=("CHECKOUT", "RESULTS", "PLAN"),
                        help="Current version checkout, raw trial directory and run plan")
    parser.add_argument("--focused-results", type=Path, help="Raw records for reviewed supplemental samples")
    parser.add_argument("--materials-dir", type=Path, default=ROOT / "交付", help="方案 PDF、演示视频和捕获记录的目录")
    parser.add_argument("--output", type=Path, default=ROOT / "交付/重邮GGBond-F01-工程复核包.zip")
    args = parser.parse_args()
    build(args.results, args.release_dir, args.output, args.capture_dir,
          args.regression_results, args.regression_summary, args.primary_results, args.primary_summary, args.materials_dir,
          args.p1b_checkout, args.p1c_checkout, args.release_regression, args.focused_results)


if __name__ == "__main__":
    main()
