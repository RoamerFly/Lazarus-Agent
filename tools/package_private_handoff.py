"""Assemble a same-build companion review package for the P06 private handoff."""
from argparse import ArgumentParser
from hashlib import sha256
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent_acceptance import verified_for
from tools.replay_agent_edit_trace import sha256 as digest_file
from tools.replay_record_caller import CALLER, DEFAULT_AUTHORITY, FOREIGN_PROVIDER, TARGET

MODE = "offline-replayed-private-handoff"
SAMPLE = "可分发样本/P06/"
BOUNDARY = "外部调用探针/"
EDITS = "可复跑轨迹/"
TRACE_NAME = "P06-private-receiver.json"
TRACE = ROOT / "docs/validation/recorded-edit-fixtures" / TRACE_NAME
SOURCE_APK = "fixtures/archive-reader/archive-reader-file-share.apk"
PROBE_SOURCE = ROOT / "tools/fixtures/record-caller"
PROBE_FILES = ("AndroidManifest.xml", "BoundaryActivity.java")
EXPECTED_STEPS = [
    {"action": "tap", "value": "Share local record"}, {"action": "expect", "value": "Shared"},
    {"action": "cold_start"}, {"action": "tap", "value": "Share local record"},
    {"action": "expect", "value": "Shared"},
]


def verify_boundary(boundary, apk_hash, probe_hash):
    if (boundary["status"] != "boundary-verified" or boundary["candidate_sha256"] != apk_hash
            or boundary["network_model_calls"] != 0):
        raise ValueError("P06 接收边界证据与候选不一致")
    fixed, foreign = (boundary[key] for key in ("fixed_probe_uri", "foreign_provider"))
    for run in (fixed, foreign):
        if (run["api"] != 36 or run["abis"] != ["x86_64"] or run["candidate_and_installed_sha256"] != apk_hash
                or run["caller_apk_sha256"] != probe_hash or set(run["uids"]) != {TARGET, CALLER}
                or len(set(run["uids"].values())) != 2):
            raise ValueError("P06 外部应用身份或设备回读不一致")
        steps = run["in_app_share"]
        if ([(step["action"], step.get("value"), step["status"]) for step in steps]
                != [("cold_start", None, "passed"), ("tap", "Share local record", "passed"),
                    ("expect", "Shared", "passed")] or steps[-1]["match_count"] != 1):
            raise ValueError("P06 缺少探针会话中的合法交接证据")
    if (fixed["record_uri"] != f"content://{DEFAULT_AUTHORITY}/record.txt"
            or foreign["record_uri"] != FOREIGN_PROVIDER + "/record"
            or foreign["alternate_foreign_uri"] != FOREIGN_PROVIDER + "/other"):
        raise ValueError("P06 外部探针地址不一致")
    observations = {"固定地址交接前": fixed["before_share"], "固定地址交接后": fixed,
                    "伪造字节": fixed["forged_bytes_after"], "外部记录交接前": foreign["before_share"],
                    "外部记录交接后": foreign, "不同字节记录": foreign["alternate_foreign_value"]}
    for name, row in observations.items():
        nodes = {tuple(node) for node in row["observed"]}
        if (row["outcome"] != "target-rejected" or (TARGET, "Rejected") not in nodes
                or (TARGET, "Shared") in nodes):
            raise ValueError(f"P06 外部输入未被接收端明确拒绝（{name}，{row['outcome']}）")
    return len(observations)


def verify_private_handoff(archive, checksums, status):
    trace_name = EDITS + TRACE_NAME
    if status.get("mode") != MODE:
        if trace_name in checksums:
            raise ValueError("P06 专项包缺少接收边界验收声明")
        return None
    build = json.loads(archive.read("本地构建回执.json"))
    report = json.loads(archive.read(SAMPLE + "agent-acceptance.json"))
    edit = json.loads(archive.read(EDITS + "构建回执.json"))
    trace = json.loads(archive.read(trace_name))
    candidate_hash = checksums[SAMPLE + "candidate.apk"]
    source_hash = checksums[SAMPLE + "base.apk"]
    if (any(status[key] != build[key] for key in ("version", "source_commit", "exe_sha256"))
            or build["self_test_exit"] != 0 or status["network_model_calls"] != 0
            or status["candidate_sha256"] != candidate_hash or status["source_sha256"] != source_hash
            or edit["implementation_commit"] != build["source_commit"] or edit["network_model_calls"] != 0
            or edit["input_sha256"] != source_hash or edit["candidate_sha256"] != candidate_hash
            or edit["trace_sha256"] != checksums[trace_name] or edit["changes"] != trace["changes"]
            or trace["source_sha256"] != source_hash or trace["outcome"]["outcome"] != "candidate"):
        raise ValueError("P06 构建、工具轨迹或样本身份不一致")
    row = report["rows"][0]
    device = row["candidate"]["device"]
    if (report["steps"] != EXPECTED_STEPS or report["targets"] != [36] or row["baseline"]["install"] != "failed"
            or not verified_for(report, candidate_hash) or device["api"] != 36 or device["abis"] != ["x86_64"]
            or device["installed_sha256"] != candidate_hash):
        raise ValueError("P06 应用内设备验收不一致")
    boundary = json.loads(archive.read(BOUNDARY + "结果.json"))
    if any(BOUNDARY + "源代码/" + name not in checksums for name in PROBE_FILES):
        raise ValueError("P06 缺少外部探针源码")
    rejects = verify_boundary(boundary, candidate_hash, checksums[BOUNDARY + "caller.apk"])
    return {"app_steps": len(report["steps"]), "external_rejections": rejects,
            "candidate_sha256": candidate_hash, "network_model_calls": 0}


def build_package(delivery, review, checkout, rebuilt, probe, output):
    from tools.package_agent_review import FILE_CHECK_SCRIPT
    from tools.verify_review_package import verify

    if output.exists():
        raise ValueError("请选择新的 P06 评审包路径")
    build_path = delivery / "本地构建回执.json"
    build = json.loads(build_path.read_bytes())
    prior = verify(review)
    if (not prior["same_build_evidence"]
            or any(prior["package_status"][key] != build[key] for key in ("version", "source_commit", "exe_sha256"))
            or build["exe_sha256"] != digest_file(delivery / "Lazarus-Agent.exe")):
        raise ValueError("评审包与同版 EXE 身份不一致")
    files = {"本地构建回执.json": build_path.read_bytes(),
             SAMPLE + "base.apk": (checkout / SOURCE_APK).read_bytes(),
             SAMPLE + "candidate.apk": (rebuilt / "candidate.apk").read_bytes(),
             SAMPLE + "agent-acceptance.json": (rebuilt / "exe-acceptance.json").read_bytes(),
             BOUNDARY + "caller.apk": (probe / "caller.apk").read_bytes(),
             BOUNDARY + "结果.json": (probe / "result.json").read_bytes(),
             EDITS + TRACE_NAME: TRACE.read_bytes(), EDITS + "构建回执.json": (rebuilt / "receipt.json").read_bytes(),
             EDITS + "changes.diff": (rebuilt / "changes.diff").read_bytes(),
             "校验文件.ps1": FILE_CHECK_SCRIPT.encode("utf-8-sig"),
             "复测样本.ps1": (ROOT / "tools/review_replay.ps1").read_text(encoding="utf-8-sig").encode("utf-8-sig")}
    files.update({BOUNDARY + "源代码/" + name: (PROBE_SOURCE / name).read_bytes() for name in PROBE_FILES})
    status = {key: build[key] for key in ("version", "source_commit", "exe_sha256")}
    status.update(mode=MODE, network_model_calls=0, review_archive_sha256=digest_file(review),
                  source_sha256=sha256(files[SAMPLE + "base.apk"]).hexdigest(),
                  candidate_sha256=sha256(files[SAMPLE + "candidate.apk"]).hexdigest())
    files["复核状态.json"] = json.dumps(status, ensure_ascii=False, indent=2).encode()
    files["从这里开始.md"] = guide(status).encode()
    files["SHA256SUMS.txt"] = "".join(f"{sha256(data).hexdigest()}  {name}\n"
                                     for name, data in sorted(files.items())).encode()
    with TemporaryDirectory(dir=output.parent, prefix="private-handoff-review-") as directory:
        staged = Path(directory) / "review.zip"
        with ZipFile(staged, "x", ZIP_DEFLATED) as archive:
            for name, data in sorted(files.items()):
                archive.writestr(ZipInfo(name), data, compress_type=ZIP_DEFLATED)
        checked = verify(staged)
        if not checked["private_handoff"] or len(checked["samples"]) != 1:
            raise ValueError("P06 评审包缺少同版证据")
        staged.rename(output)
    return {**status, **checked["private_handoff"], "archive": output.name,
            "archive_sha256": digest_file(output), "registered_files": checked["file_count"] - 1}


def guide(status):
    return fr"""# v{status['version']} P06 私有文件交接专项

本包使用自有原 APK 和历史模型留档编辑。在 v{status['version']} 重建签名后，由同版 EXE 验收应用内五步。另一 UID 的探针验证两次固定地址、一次伪造字节及三次可读的外部记录均被拒绝。本轮模型调用为零。完整评审包的版本及哈希见[复核状态](复核状态.json)。

解包后使用同一评审 EXE。以下路径按实际位置替换，准备好 Android 16 专用模拟器后可去掉 `-CheckOnly`。

    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\校验文件.ps1
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\复测样本.ps1 -LocalBuild -App "..\Lazarus-Agent.exe" -Sample P06 -CheckOnly

[设备验收报告](可分发样本/P06/agent-acceptance.json) · [外部探针结果](外部调用探针/结果.json) · [探针实现](外部调用探针/源代码/BoundaryActivity.java) · [留档编辑](可复跑轨迹/{TRACE_NAME})

完整复跑使用工程验证分支的 `tools/replay_agent_edit_trace.py` 与 `tools/replay_record_caller.py`，后者指定 `--private-boundary`。外部探针在专用环境安装自有测试应用，并检查接收端的明确拒绝状态。
"""


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    for field in ("delivery", "review", "checkout", "rebuilt", "probe", "output"):
        parser.add_argument("--" + field, type=Path, required=True)
    print(json.dumps(build_package(**vars(parser.parse_args())), ensure_ascii=False, indent=2))
