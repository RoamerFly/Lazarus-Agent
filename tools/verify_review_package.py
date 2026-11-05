"""Verify review ZIP bytes and packaged APK proofs without extracting executable files."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_acceptance import verified_for


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
        return {"status": "archive-and-proofs-verified", "file_count": len(names),
                "samples": samples, "package_status": json.loads(archive.read("复核状态.json")),
                "scope": "Files, checksums and stored proofs. Device replay is a separate operation."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package")
    args = parser.parse_args()
    print(json.dumps(verify(args.package), ensure_ascii=False, indent=2))
