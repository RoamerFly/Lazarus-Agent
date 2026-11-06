"""Verify review ZIP bytes and packaged APK proofs without extracting executable files."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
import sys
from urllib.parse import unquote, urlsplit
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_acceptance import verified_for


def check_local_links(archive, names):
    count = 0
    for name in names:
        if not name.endswith(".md"):
            continue
        for link in re.findall(r"\]\(([^\s)]+)\)", archive.read(name).decode("utf-8-sig")):
            parsed = urlsplit(link)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            target = posixpath.normpath(posixpath.join(posixpath.dirname(name), unquote(parsed.path)))
            if target not in names:
                raise ValueError(f"复核文档引用缺失文件 {name} → {target}")
            count += 1
    return count


def verify(path):
    with ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("复核包有重名条目")
        for name in names:
            parts = PurePosixPath(name).parts
            if name.startswith(("/", "\\")) or "\\" in name or ":" in name or ".." in parts:
                raise ValueError("复核包包含不安全路径")
        checksums = {}
        for row in archive.read("SHA256SUMS.txt").decode("utf-8").splitlines():
            checksum, name = row.split("  ", 1)
            if name in checksums or name not in names:
                raise ValueError("校验清单重复或引用缺失文件")
            checksums[name] = checksum
        if set(checksums) != set(names) - {"SHA256SUMS.txt"}:
            raise ValueError("文件与校验清单不完全对应")
        for name, expected in checksums.items():
            with archive.open(name) as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                    raise ValueError("文件哈希不一致：" + name)
        samples = []
        for name in names:
            if "可分发样本" in PurePosixPath(name).parts and name.endswith("/agent-acceptance.json"):
                folder = name.rsplit("/", 1)[0]
                report = json.loads(archive.read(name))
                source = hashlib.sha256(archive.read(folder + "/base.apk")).hexdigest()
                candidate = hashlib.sha256(archive.read(folder + "/candidate.apk")).hexdigest()
                if source != report["input_sha256"] or not verified_for(report, candidate):
                    raise ValueError("样本的文件与独立验收不一致：" + folder)
                samples.append({"sample": folder, "candidate_sha256": candidate, "targets": report["targets"]})
        if not samples:
            raise ValueError("复核包缺少带完整验收报告的样本")
        counterexamples = []
        for name in names:
            if "反例样本" not in PurePosixPath(name).parts or not name.endswith("/agent-acceptance.json"):
                continue
            folder = name.rsplit("/", 1)[0]
            report = json.loads(archive.read(name))
            expected = json.loads(archive.read(folder + "/expected-outcome.json"))
            source = hashlib.sha256(archive.read(folder + "/base.apk")).hexdigest()
            candidate = hashlib.sha256(archive.read(folder + "/candidate.apk")).hexdigest()
            plan = expected["steps"]
            if (len(plan) != 2 or [s["action"] for s in plan] != ["expect", "absent"] or
                    plan[0]["value"] != plan[1]["value"] or
                    [s["status"] for s in plan] != ["passed", "failed"] or
                    any(s["match_count"] < 1 for s in plan) or
                    report["steps"] != [{k: s[k] for k in ("action", "value")} for s in plan]):
                raise ValueError("反例没有声明可复核的矛盾断言：" + folder)
            if (source != report["input_sha256"] or candidate != report["candidate_sha256"] or
                    report["status"] != "incomplete" or expected["exit_code"] != 1 or
                    len(report["rows"]) != len(report["targets"]) or not report["targets"]):
                raise ValueError("反例身份、目标或未通过状态无效：" + folder)
            targets = report["targets"]
            if any(type(api) is not int for api in targets) or len(targets) != len(set(targets)):
                raise ValueError("反例目标版本无效：" + folder)
            for api, row in zip(targets, report["rows"]):
                proof = row["candidate"]
                if row["api"] != api or proof["device"]["api"] != api:
                    raise ValueError("反例设备版本与复测目标不一致：" + folder)
                observed = [{k: step[k] for k in ("action", "value", "status", "match_count")}
                            for step in proof["steps"]]
                if (row["status"] != "failed" or proof["install"] != "passed" or proof["launch"] != "passed" or
                        proof["function"] != "failed" or proof["device"]["installed_sha256"] != candidate or
                        observed != expected["steps"]):
                    raise ValueError("反例没有对应到指定的行为失败：" + folder)
            counterexamples.append({"sample": folder, "candidate_sha256": candidate, "status": "incomplete"})
        return {"status": "archive-and-proofs-verified", "file_count": len(names),
                "local_links_checked": check_local_links(archive, set(names)),
                "samples": samples, "counterexamples": counterexamples,
                "package_status": json.loads(archive.read("复核状态.json")),
                "scope": "Files, checksums and stored proofs. Device replay is a separate operation."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package")
    args = parser.parse_args()
    print(json.dumps(verify(args.package), ensure_ascii=False, indent=2))
