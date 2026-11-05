"""Audit raw F01 trial evidence against its frozen protocol."""
from argparse import ArgumentParser
import ast
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tools")]

from agent_acceptance import verified_for
from agent_workspace import digest_file
from tools.run_f01_p1 import plan, trial_schedule
from tools.summarize_f01_p1 import load, price_bounds, summarize


FREEZE = "37f543af17923ab0f59523bce349f34501659bd3"
IMPLEMENTATION = "781fb4bc035fff44f7c20d250636424c04c2a4ed"
SPEC = Path("tools/f01-p1b-cases.json")
REGISTRATION = Path("docs/science-superpowers/preregistrations/2026-11-06-f01-p1b.md")


@dataclass(frozen=True)
class FrozenBatch:
    freeze: str
    implementation: str
    version: str
    spec: Path
    registration: Path
    results: Path


P1B = FrozenBatch(FREEZE, IMPLEMENTATION, "0.6.15", SPEC, REGISTRATION, Path("build/p1b"))


def git_at(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args])


def p1c_batch(root):
    registration = Path("docs/science-superpowers/preregistrations/2026-11-06-f01-p1c.md")
    commits = git_at(root, "log", "--diff-filter=A", "--format=%H", "--", registration.as_posix()).decode().splitlines()
    if len(commits) != 1:
        raise ValueError("P1C 预注册必须有唯一的首次冻结提交")
    return FrozenBatch(commits[0], "1a7bbbeed8e2b75386f7d8b79f61bed091458440", "0.6.15-dev",
                       Path("tools/f01-p1c-cases.json"), registration, Path("build/p1c"))


def frozen_protocol(root, batch=P1B):
    if git_at(root, "rev-parse", "HEAD").decode().strip() != batch.freeze:
        raise ValueError("评测检出目录与冻结提交不符")
    if git_at(root, "status", "--porcelain"):
        raise ValueError("冻结检出目录含未归档的源码变更")
    registration = git_at(root, "show", f"{batch.freeze}:{batch.registration.as_posix()}")
    if (ROOT / batch.registration).read_bytes() != registration:
        raise ValueError("当前预注册文档与冻结文本不符")
    def score_contract(path):
        module = ast.parse(path.read_bytes())
        return {item.name: ast.dump(item) for item in module.body
                if isinstance(item, ast.FunctionDef) and item.name in {"parse_steps", "verified_for"}}
    if score_contract(ROOT / "agent_acceptance.py") != score_contract(root / "agent_acceptance.py"):
        raise ValueError("验收计分逻辑与该批冻结版本不符")
    checksum_lines = registration.decode("utf-8").split("## Frozen data checksums\n", 1)[1]
    for line in checksum_lines.splitlines():
        if not line:
            continue
        _, name, expected = line.split()
        for location in (root, ROOT):
            observed = git_at(location, "hash-object", "--", name).decode().strip()
            if observed != expected:
                raise ValueError("冻结输入校验失败：" + name)
    return load(root / batch.spec)


def audit(root, *, partial=False, batch=P1B):
    return audit_records(root, batch, frozen_protocol(root, batch), partial=partial)


def audit_records(root, batch, protocol, *, partial=False):
    results = root / batch.results
    if not partial and (results / "running.lock").exists():
        raise ValueError("请在进程结束后生成正式汇总")
    summary = summarize(results, protocol)
    trials = list(trial_schedule(protocol))
    source_hashes = {case["id"]: digest_file(root / case["apk"]) for case in protocol["cases"]}
    trial_ids = {f"{case['id']}-{mode}-{repeat}" for case, mode, repeat in trials}
    if {path.name for path in results.iterdir() if path.is_dir()} - trial_ids:
        raise ValueError("结果目录含有固定日程之外的轮次")
    if len(trials) != 42 or not partial and any(not (results / trial).is_dir() for trial in trial_ids):
        raise ValueError("固定日程尚未全部尝试")

    responses = []
    for (case, mode, repeat), row in zip(trials, summary["rows"]):
        trial = f"{case['id']}-{mode}-{repeat}"
        folder = results / trial
        source = source_hashes[case["id"]]
        identity = {"case": case["id"], "class": case["class"], "mode": mode, "repeat": repeat,
                    "api": protocol["target"], "source_sha256": source}
        started = load(folder / "started.json")
        result = load(folder / "result.json")
        for record in (started, result):
            if record and any(record.get(key) != value for key, value in identity.items()):
                raise ValueError("轮次身份与冻结日程不符：" + trial)
        if result and not started:
            raise ValueError("轮次缺少启动记录：" + trial)

        usage = load(folder / "usage.json")
        if result and (usage or []) != result.get("model_usage", []):
            raise ValueError("模型计量与轮次终态不符：" + trial)
        responses.extend(usage or [])
        proof = load(folder / "acceptance.json")
        sha = (result.get("result") or {}).get("sha256")
        if result.get("behavior_verified") and not (sha and proof):
            raise ValueError("轮次声称通过但缺少候选及验收报告：" + trial)
        if result and (sha or proof):
            if (not sha or not proof or digest_file(folder / "candidate.apk") != sha
                    or proof.get("candidate_sha256") != sha or proof.get("input_sha256") != source
                    or proof.get("targets") != [protocol["target"]] or proof.get("steps") != plan(case["steps"])):
                raise ValueError("候选、设备计划或验收报告不一致：" + trial)
            if verified_for(proof, sha) != bool(result.get("behavior_verified")):
                raise ValueError("保存的通过标记与独立验收不符：" + trial)
            if len(proof["rows"]) != 1 or result.get("comparison") != proof["rows"][0].get("comparison"):
                raise ValueError("基准比较与独立验收不符：" + trial)
            for observed in proof["rows"]:
                for field, expected in (("baseline", source), ("candidate", sha)):
                    stage = observed.get(field) or {}
                    device = stage.get("device") or {}
                    if stage.get("install") == "passed" and (
                            device.get("installed_sha256") != expected or "x86_64" not in device.get("abis", [])):
                        raise ValueError("安装回读哈希或设备 ABI 不一致：" + trial)

        row.update(trial=trial, terminal=bool(result), candidate_sha256=sha,
                   candidate_status=result.get("candidate_status"), abi=result.get("abis"),
                   tool_error=result.get("error"),
                   raw_result_sha256=digest_file(folder / "result.json") if result else None,
                   raw_acceptance_sha256=digest_file(folder / "acceptance.json") if proof else None,
                   raw_diff_sha256=digest_file(folder / "changes.diff") if (folder / "changes.diff").is_file() else None)

    counts = summary["counts"]
    held_out = sum(row["state"] == "passed" and row["mode"] == "agent" and row["case"] in protocol["held_out"]
                   for row in summary["rows"])
    gates = {"positives": counts["agent_positives"] >= 15, "primary": counts["primary"] == 3,
             "negatives": counts["agent_negative_rejected"] == 6 and counts["agent_false_acceptances"] == 0,
             "held_out": held_out >= 2}
    return {
        "schema": 1, "app_version": batch.version, "freeze_commit": batch.freeze,
        "implementation_commit": batch.implementation,
        "evaluation_role": protocol["evaluation_role"], "protocol_sha256": digest_file(root / batch.spec),
        "model": protocol["model"], "target_api": protocol["target"], "source_apk_sha256": source_hashes,
        "planned_trials": len(trials), "terminal_trials": sum(row["terminal"] for row in summary["rows"]),
        "held_out_passes": held_out, "joint_gate": None if partial else all(gates.values()),
        "gate_checks": None if partial else gates,
        "model_responses_recorded": len(responses),
        "recorded_chat_cost_usd_range": price_bounds(responses),
        "model_fingerprints": sorted({row["fingerprint"] for row in responses if row.get("fingerprint")}),
        **summary,
    }


def require_diff_review(audit_result, review):
    """Require one SHA-bound semantic review for every passing candidate."""
    expected = {row["trial"]: (row["candidate_sha256"], row["raw_diff_sha256"])
                for row in audit_result["rows"] if row["state"] == "passed"}
    reviewed = {}
    for row in review["rows"]:
        if row["trial"] in reviewed or not row["review"].strip() or not row["scope"].strip():
            raise ValueError("P1B 差异复查缺少说明或重复记录")
        reviewed[row["trial"]] = row["candidate_sha256"], row["raw_diff_sha256"]
    if reviewed != expected:
        raise ValueError("P1B 差异复查未对应全部通过候选及其哈希")


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("checkout", type=Path)
    parser.add_argument("--batch", choices=("p1b", "p1c"), default="p1b")
    parser.add_argument("--partial", action="store_true", help="Audit available records without evaluating the gate")
    args = parser.parse_args()
    checkout = args.checkout.resolve()
    batch = p1c_batch(checkout) if args.batch == "p1c" else P1B
    print(json.dumps(audit(checkout, partial=args.partial, batch=batch), ensure_ascii=False, indent=2))
