"""Assemble a review-only entry from an explicit allowlist; never copy a user signing key."""
from pathlib import Path
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from zipfile import ZipFile, ZIP_DEFLATED
from audit_workbench import verify

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"交付"
DEFAULT_MODIFICATION_INPUTS={
    "remember-file":"fixtures/file-reader/file-reader-forgets-selection.apk",
    "localize-zh":"fixtures/pocket-list/pocket-list-en.apk",
}
def copy_file(source,destination):
    destination.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(source,destination)
def git(repo,*args):
    return subprocess.check_output(["git","-C",str(repo),*args],stderr=subprocess.STDOUT)
def hash_file(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()
def zip_tree(folder,out):
    with ZipFile(out,"w",compression=ZIP_DEFLATED) as z:
        for p in sorted(folder.rglob("*")):
            if p.is_file():
                z.write(p,p.relative_to(folder))

def validated_modification_cases():
    """Return only review artifacts declared and hash-pinned by the v0.4 report."""
    report=ROOT/"docs/validation/0.4.0-modifications.json"
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
            path=(ROOT/raw).resolve()
            if ROOT not in path.parents or not path.is_file():
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
            proof=(ROOT/item["report"]).resolve()
            if ROOT not in proof.parents or not proof.is_file():
                raise ValueError("v0.4 验证报告不在项目内："+name)
        verified.append((name,paths[0],paths[1],proof))
    kinds={item.get("kind") for item in cases if isinstance(item,dict)}
    if not {"remember-file","localize-zh"}.issubset(kinds):
        raise ValueError("v0.4 验证记录必须包含行为与汉化用例")
    return report,verified

def build():
    verify()
    modification_report, modification_cases=validated_modification_cases()
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp)
        repo=root/"source";repo.mkdir()
        kit=root/"entry";kit.mkdir()
        # Use the public source inventory so new runtime modules and build
        # helpers cannot disappear from the competition snapshot.
        for name in git(ROOT,"ls-files","-z").decode("utf-8").split("\0"):
            if name:
                copy_file(ROOT/name,repo/name)
        copy_file(ROOT/"old_apk/OI-Notepad-1.3.apk",repo/"old_apk/OI-Notepad-1.3.apk")
        for p in (ROOT/"artifacts/candidates").iterdir():
            if p.is_file() and p.suffix in {".txt",".md",".apk"}:
                copy_file(p,repo/p.relative_to(ROOT))
        for folder in (ROOT/"evidence").iterdir():
            if folder.is_file() and folder.name in {"legacy-preview.json","android16-device.json"}:
                copy_file(folder,repo/folder.relative_to(ROOT))
            elif folder.is_dir() and (folder.name.startswith(("api34-","api35-","api36-")) or folder.name in {"peer-web","peer-cli","release-replays","ttpod","foreground"}):
                for p in folder.rglob("*"):
                    if not p.is_file():continue
                    if p.suffix.lower() in (".png",".json",".xml",".md",".txt") or (folder.name=="peer-web" and p.name.endswith("-download.apk")):
                        copy_file(p,repo/p.relative_to(ROOT))
        for relative in ("evidence/pacific-rim/android16/report.json",
                         "evidence/pacific-rim/android16-pic/report.json",
                         "evidence/pacific-rim/pic-trial/binary-verification.json",
                         "evidence/pacific-rim/android16-official-images.json",
                         "evidence/pacific-rim/redroid16-native-arm-boot.json",
                         "evidence/pacific-rim/redroid16-native-arm-original.json",
                         "evidence/pacific-rim/redroid16-native-arm-game.json",
                         "evidence/pacific-rim/redroid16-game-ui/observations.json",
                         "evidence/pacific-browser/verified/report.json",
                         "evidence/pacific-browser/verified/negative.json",
                         "evidence/pacific-browser/verified/limits.json",
                         "evidence/pacific-rim/android11-original.json",
                         "evidence/pacific-rim/android11-candidate.json",
                         "evidence/native-proxy-acceptance/verified-api36/report.json"):
            copy_file(ROOT/relative,repo/relative)
        # Redact workstation account and local task roots in distributed text copies.
        for p in (repo/"evidence").rglob("*"):
            if p.suffix not in {".json",".md",".txt"}:continue
            try:t=p.read_text(encoding="utf-8-sig")
            except UnicodeDecodeError:continue
            for old,new in [(str(Path.home()),"<USER_HOME>"),(str(ROOT),"<PROJECT>")]:
                t=t.replace(old.replace("\\","\\\\"),new).replace(old,new)
            p.write_text(t,encoding="utf-8")
        zip_tree(repo,kit/"F01-源代码.zip")
        git(repo,"init","-b","main")
        git(repo,"-c","core.autocrlf=false","add","--force",".")
        git(repo,"-c","user.name=APK Reviver","-c","user.email=build@localhost",
            "-c","commit.gpgSign=false","commit","-m","F01 offline review snapshot")
        git(repo,"bundle","create",str(kit/"F01-源代码.bundle"),"--all")
        clone=root/"clone"
        subprocess.run(["git","clone","-q",str(kit/"F01-源代码.bundle"),str(clone)],check=True)
        subprocess.run(["python","tools/audit_workbench.py"],cwd=clone,check=True)
        subprocess.run(["python","tools/audit_advisor.py"],cwd=clone,check=True)
        for filename in ("遗留软件复活智能体方案说明.pdf","遗留软件复活智能体演示.mp4"):
            copy_file(OUT/filename,kit/filename)
        for filename in ("初赛复核说明.md","运行指南.md"):
            copy_file(ROOT/filename,kit/filename)
        copy_file(ROOT/"dist/Lazarus-Agent.exe",kit/"Windows桌面版/Lazarus-Agent.exe")
        copy_file(modification_report,kit/"功能改造验证/0.4.0-modifications.json")
        copy_file(modification_report.with_name("0.4.0-界面验收.zip"),kit/"功能改造验证/0.4.0-界面验收.zip")
        for name,source,artifact,proof in modification_cases:
            destination=kit/"可分发样本"/"通用功能改造"/name
            copy_file(source,destination/("输入样本"+source.suffix))
            copy_file(artifact,destination/("验证导出"+artifact.suffix))
            if proof:
                copy_file(proof,destination/"验证记录.json")
        for p in (ROOT/"artifacts/candidates").iterdir():
            if p.suffix in {".txt",".md",".apk"}:copy_file(p,kit/"可分发样本"/p.name)
        copy_file(ROOT/"evidence/peer-web/archive-broken-api35-download.apk",kit/"可分发样本/团队归档读取器-修复版-api35.apk")
        share=ROOT/"evidence/release-replays/file-share-34-_ynb137f"
        share_report=json.loads((share/"report.json").read_text(encoding="utf-8"))
        assert hash_file(share/"export.apk")==share_report["export"]["sha256"]
        copy_file(share/"export.apk",kit/"可分发样本/团队文件分享-修复版-api34.apk")
        native=ROOT/"evidence/native-proxy-acceptance/verified-api36"
        native_report=json.loads((native/"report.json").read_text(encoding="utf-8"))
        assert native_report["tests"][0]["functional_test"]["native_crash"] and native_report["acceptance"]["result"]=="passed"
        assert hash_file(native/"verified.apk")==native_report["export"]["sha256"]
        copy_file(native/"verified.apk",kit/"可分发样本/原生代理库-修复版-api36.apk")
        (kit/"SHA256SUMS.txt").write_text("\n".join(f"{hash_file(p)}  {p.relative_to(kit).as_posix()}"
            for p in sorted(kit.rglob("*")) if p.is_file())+"\n",encoding="utf-8")
        result=OUT/"重邮GGBond-F01-初赛复核包.zip"
        with tempfile.NamedTemporaryFile(dir=OUT,suffix=".partial",delete=False) as tmp:
            staged=Path(tmp.name)
        try:
            zip_tree(kit,staged)
            with ZipFile(staged) as z:assert z.testzip() is None
            staged.replace(result)
        finally:
            staged.unlink(missing_ok=True)
        print(result,result.stat().st_size,hash_file(result))

if __name__=="__main__":build()
