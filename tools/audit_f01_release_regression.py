"""Audit current release behavior on the already observed F01 P1C cases."""
from argparse import ArgumentParser
from dataclasses import replace
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent_workspace import digest_file
from tools.audit_f01_p1b import audit_records, git_at, p1c_batch
from tools.summarize_f01_p1 import load

ROLE = "development-regression-on-seen-cases"
FOCUSED_ROLE = "focused development replay; source only"


def fixed_protocol(checkout, historical, commit, protocol_sha256):
    if (ROOT / "agent_acceptance.py").read_bytes() != (checkout / "agent_acceptance.py").read_bytes():
        raise ValueError("发布回归必须使用候选版本的验收逻辑")
    spec = checkout / historical.spec
    if protocol_sha256 != digest_file(spec):
        raise ValueError("发布回归的方案哈希不符")
    protocol = load(spec)
    fixed = [historical.registration, historical.spec, Path("tools/run_f01_p1.py"),
             Path("tools/summarize_f01_p1.py"),
             *(Path(case["apk"]) for case in protocol["cases"])]
    if git_at(checkout, "diff", "--name-only", historical.freeze, commit, "--", *(str(path) for path in fixed)):
        raise ValueError("发布回归必须复用原有方案、执行器和输入样本")
    return protocol


def result_batch(checkout, historical, results, commit, version):
    results = results.resolve()
    if not results.is_relative_to(checkout / "build"):
        raise ValueError("发布回归结果必须在检出目录的 build 下")
    return replace(historical, implementation=commit, version=version, results=results.relative_to(checkout))


def audit_release_regression(checkout, results, record):
    historical = p1c_batch(checkout)
    commit = git_at(checkout, "rev-parse", "HEAD").decode().strip()
    version = record["app_version"]
    if (git_at(checkout, "status", "--porcelain") or commit != record["implementation_commit"]
            or f'APP_VERSION = "{version}"'.encode() not in git_at(checkout, "show", f"{commit}:app_version.py")
            or record["source_protocol_freeze_commit"] != historical.freeze or record["purpose"] != ROLE):
        raise ValueError("发布回归的检出版本、提交或评测用途不符")
    protocol = fixed_protocol(checkout, historical, commit, record["protocol_sha256"])
    batch = result_batch(checkout, historical, results, commit, version)
    audited = audit_records(checkout, batch, {**protocol, "evaluation_role": ROLE})
    if audited["terminal_trials"] != audited["planned_trials"] or audited["planned_trials"] != record["planned_trials"]:
        raise ValueError("发布回归缺少完整的轮次终态")
    return {**audited, "source_build_run": record["build_run"]}


def audit_focused_regression(checkout, results, record):
    historical = p1c_batch(checkout)
    commit = git_at(checkout, "rev-parse", "HEAD").decode().strip()
    if (git_at(checkout, "status", "--porcelain") or commit != record["source_commit"]
            or record["freeze_commit"] != historical.freeze or record["role"] != FOCUSED_ROLE):
        raise ValueError("专项回归的源码提交、冻结记录或用途不符")
    protocol = fixed_protocol(checkout, historical, commit, record["protocol_sha256"])
    selection = record["cases"]
    if not isinstance(selection, list) or not all(isinstance(case, str) for case in selection):
        raise ValueError("专项计划必须列出用例编号")
    cases = [case for case in protocol["cases"] if case["id"] in selection]
    if (not cases or len(cases) != len(selection) or len(cases) == len(protocol["cases"])
            or type(record["trials"]) is not int or not 0 < record["trials"] < 42):
        raise ValueError("专项计划必须列出唯一的冻结用例子集及轮次")
    batch = result_batch(checkout, historical, results, commit, None)
    audited = audit_records(checkout, batch, {**protocol, "cases": cases, "evaluation_role": FOCUSED_ROLE},
                            expected_trials=record["trials"])
    if audited["terminal_trials"] != record["trials"]:
        raise ValueError("专项回归缺少完整的轮次终态")
    return {**audited, "selected_cases": selection, "source_build_run": None}


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("checkout", type=Path)
    parser.add_argument("results", type=Path)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--focused", action="store_true", help="Audit a declared subset without evaluating the release gate")
    args = parser.parse_args()
    audit = audit_focused_regression if args.focused else audit_release_regression
    print(json.dumps(audit(args.checkout.resolve(), args.results, load(args.plan)),
                     ensure_ascii=False, indent=2))
