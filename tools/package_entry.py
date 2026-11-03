"""Assemble a review-only entry from an explicit allowlist; never copy a user signing key."""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import tempfile
from zipfile import ZipFile, ZIP_DEFLATED
from audit_workbench import verify

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"交付"
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

def build():
    verify()
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
