"""Assemble a review-only entry from an explicit allowlist; never copy a user signing key."""
from pathlib import Path
import hashlib
import json
import re
from package_agent_review import main as build_review

ROOT=Path(__file__).resolve().parents[1]
DEFAULT_MODIFICATION_INPUTS={
    "remember-file":"fixtures/file-reader/file-reader-forgets-selection.apk",
    "localize-zh":"fixtures/pocket-list/pocket-list-en.apk",
}
def hash_file(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def validated_modification_cases():
    """Return only review artifacts declared and hash-pinned by the v0.4 report."""
    project_root=ROOT.resolve(strict=True)
    report=project_root/"docs/validation/0.4.0-modifications.json"
    if not report.is_file():
        raise ValueError("缺少 v0.4 功能改造验证记录，不能制作复核包")
    try:
        document=json.loads(report.read_text(encoding="utf-8"))
        cases=document["cases"]
    except (OSError,json.JSONDecodeError,KeyError,TypeError) as exc:
        raise ValueError("v0.4 功能改造验证记录格式无效") from exc
    if not isinstance(cases,list) or len(cases)<2:
        raise ValueError("v0.4 验证记录必须包含行为与汉化两个用例")
    verified=[]
    for item in cases:
        if not isinstance(item,dict):
            raise ValueError("v0.4 验证用例格式无效")
        name=item.get("name")
        if not isinstance(name,str) or not name or len(name)>80 or any(c in name for c in "\\/:"):
            raise ValueError("v0.4 验证用例名称无效")
        kind=item.get("kind")
        raw_input=item.get("input",DEFAULT_MODIFICATION_INPUTS.get(kind))
        raw_artifact=item.get("artifact")
        if not isinstance(raw_input,str) or not raw_input or not isinstance(raw_artifact,str) or not raw_artifact:
            raise ValueError("v0.4 验证用例缺少输入样本或 artifact")
        paths=[]
        for raw in (raw_input,raw_artifact):
            if Path(raw).is_absolute():
                raise ValueError("验证文件必须使用项目相对路径")
            path=(project_root/raw).resolve()
            if project_root not in path.parents or not path.is_file():
                raise ValueError("v0.4 验证文件不在项目内："+raw)
            paths.append(path)
        expected=item.get("sha256")
        if not isinstance(expected,str) or not re.fullmatch(r"[0-9a-f]{64}",expected):
            raise ValueError("v0.4 导出 APK 哈希无效")
        if hash_file(paths[1])!=expected:
            raise ValueError("v0.4 导出 APK 哈希不匹配："+name)
        proof=None
        if "report" in item:
            if not isinstance(item["report"],str):
                raise ValueError("v0.4 验证报告路径无效")
            proof=(project_root/item["report"]).resolve()
            if project_root not in proof.parents or not proof.is_file():
                raise ValueError("v0.4 验证报告不在项目内："+name)
        verified.append((name,paths[0],paths[1],proof))
    kinds={item.get("kind") for item in cases if isinstance(item,dict)}
    if not {"remember-file","localize-zh"}.issubset(kinds):
        raise ValueError("v0.4 验证记录必须包含行为与汉化用例")
    return report,verified

def build():
    """Build the current Agent review package using explicit evidence inputs."""
    build_review()


if __name__ == "__main__":
    build()
