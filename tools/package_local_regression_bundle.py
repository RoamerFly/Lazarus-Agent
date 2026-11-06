"""Package independently replayable samples from an audited local F01 regression."""
from argparse import ArgumentParser
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.audit_f01_p1b import require_diff_review
from tools.package_agent_review import FILE_CHECK_SCRIPT, copy, digest, read
from tools.verify_review_package import verify


def add_sample(results, folder, source, trial):
    recorded = results / trial
    proof = read(recorded / "acceptance.json")
    candidate = recorded / "candidate.apk"
    if digest(source) != proof["input_sha256"] or digest(candidate) != proof["candidate_sha256"]:
        raise ValueError(f"样本与固定验收哈希不符 {trial}")
    copy(source, folder / "base.apk")
    copy(candidate, folder / "candidate.apk")
    copy(recorded / "acceptance.json", folder / "agent-acceptance.json")
    return proof


def build(source, results, plan, archive, exe, receipt, output):
    if output.exists():
        raise ValueError("请选择新的复核 ZIP 路径")
    checked = json.loads(subprocess.check_output(
        [sys.executable, "-X", "utf8", str(source / "tools/audit_f01_release_regression.py"),
         str(source), str(results), str(plan)], cwd=source))
    if checked != read(archive / "results.json"):
        raise ValueError("归档结果与固定评测原始记录不一致")
    reviews = read(archive / "diff-review.json")
    require_diff_review(checked, reviews)
    if any(type(row.get("distributable")) is not bool for row in reviews["rows"]):
        raise ValueError("候选缺少明确的分发结论")
    build_receipt = read(receipt)
    sha = digest(exe)
    version = checked["app_version"]
    if (build_receipt["version"] != version or build_receipt["source_commit"] != checked["implementation_commit"]
            or build_receipt["self_test_exit"] != 0 or build_receipt["exe_sha256"] != sha
            or checked["source_build_run"] != "local:" + sha):
        raise ValueError("本地 EXE、构建回执与评测源码不一致")

    protocol = read(source / "tools/f01-p1c-cases.json")
    cases = {row["id"]: row for row in protocol["cases"]}
    approved = {row["trial"] for row in reviews["rows"] if row["distributable"]}
    candidates = [row for row in checked["rows"] if row["mode"] == "agent" and row["trial"] in approved]
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent, prefix="f01-local-") as temporary:
        work = Path(temporary)
        kit = work / "kit"
        for row in candidates:
            trial = row["trial"]
            add_sample(results, kit / "可分发样本" / trial, source / cases[row["case"]]["apk"], trial)

        counterexample = "N03-agent-1"
        folder = kit / "反例样本" / counterexample
        negative = add_sample(results, folder, source / cases["N03"]["apk"], counterexample)
        expected = {"exit_code": 1, "steps": [
            {key: step[key] for key in ("action", "value", "status", "match_count")}
            for step in negative["rows"][0]["candidate"]["steps"]]}
        (folder / "expected-outcome.json").write_text(json.dumps(expected, ensure_ascii=False), encoding="utf-8")
        for name in ("results.json", "diff-review.json"):
            copy(archive / name, kit / name)
        copy(plan, kit / "plan.json")
        copy(source / "tools/f01-p1c-cases.json", kit / "f01-p1c-cases.json")
        copy(receipt, kit / "本地构建回执.json")
        (kit / "校验文件.ps1").write_text(FILE_CHECK_SCRIPT, encoding="utf-8-sig")
        (kit / "复测样本.ps1").write_text(
            (ROOT / "tools/review_replay.ps1").read_text(encoding="utf-8-sig"), encoding="utf-8-sig")
        metadata = {
            "mode": "local-development-replay", "version": version, "source_commit": build_receipt["source_commit"],
            "exe_sha256": sha, "protocol_sha256": checked["protocol_sha256"],
            "terminal_trials": checked["terminal_trials"], "counts": checked["counts"],
            "distributable_agent_trials": [row["trial"] for row in candidates],
            "counterexamples": [counterexample],
        }
        (kit / "复核状态.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        (kit / "复核说明.md").write_text(f"""# v{version} 本地开发回归复测

本包收录 {checked["terminal_trials"]} 轮固定开发回归的汇总、逐包语义审查和 {len(candidates)} 份通过审查的 Agent 候选。反例目录另收录一份安装、启动成功而指定行为断言失败的 APK。样本来自队伍自建的模拟场景。[计分结果](results.json) · [语义审查](diff-review.json)

先在解压目录运行下列两条命令。把示例 EXE 路径替换为同版便携文件的位置。脚本按本包构建回执核对 EXE 的 SHA-256。输入校验读取 APK 与断言，设备运行需另行执行。

    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\\校验文件.ps1
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\\复测样本.ps1 -LocalBuild -App "..\\Lazarus-Agent.exe" -CheckOnly

实际复测前，用同版 EXE 在「运行环境」准备 Android 16，准备完成后关闭工作台。在第二条命令中去掉 -CheckOnly 即可重放巡检单。可用 -Sample P04-agent-1 选择温度卡，用 -Counterexample 选择矛盾断言。反例脚本返回 0 表示观测到预期的行为失败。每次复测写入独立结果目录。

本地开发回归与正式参赛包分开归档。设备记录对应 Android 16 x86_64 专用模拟器。
""", encoding="utf-8")
        files = sorted((path.relative_to(kit).as_posix(), path) for path in kit.rglob("*") if path.is_file())
        checksums = "".join(f"{digest(path)}  {name}\n" for name, path in files)
        staged = work / "bundle.zip"
        with ZipFile(staged, "x", ZIP_DEFLATED) as zipped:
            for name, path in files:
                zipped.writestr(ZipInfo(name), path.read_bytes(), compress_type=ZIP_DEFLATED)
            zipped.writestr(ZipInfo("SHA256SUMS.txt"), checksums.encode("utf-8"), compress_type=ZIP_DEFLATED)
        validated = verify(staged)
        if (validated["local_regression"]["distributed"] != len(candidates)
                or len(validated["samples"]) != len(candidates) or len(validated["counterexamples"]) != 1):
            raise ValueError("复核 ZIP 的样本范围与审核结论不一致")
        staged.rename(output)
    return {**metadata, "bundle_sha256": digest(output), "bundle_bytes": output.stat().st_size,
            "files": validated["file_count"]}


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    for field in ("source", "results", "plan", "archive", "exe", "receipt", "output"):
        parser.add_argument("--" + field, type=Path, required=True)
    paths = {key: value.resolve() for key, value in vars(parser.parse_args()).items()}
    print(json.dumps(build(**paths), ensure_ascii=False, indent=2))
