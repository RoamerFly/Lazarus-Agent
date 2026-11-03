"""Build two team-owned engineering APKs. Uses only SDK tools, javac and local test signing."""
import os
from pathlib import Path
import sys
import tempfile
from xml.etree import ElementTree as ET
from zipfile import ZipFile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from bench_device import SDK,TOOLS
from reviver import run,test_key


def build(share=False):
    src=ROOT/"fixtures/archive-reader"
    platforms=sorted(SDK.glob("platforms/android-*/android.jar"),reverse=True)
    jar=next(p for p in platforms if (p.parent/"optional/org.apache.http.legacy.jar").is_file())
    legacy=jar.parent/"optional/org.apache.http.legacy.jar"
    env=os.environ.copy()
    key=test_key(env)
    with tempfile.TemporaryDirectory() as temp:
        tmp=Path(temp)
        classes=tmp/"classes"
        classes.mkdir()
        run(["javac","-encoding","UTF-8","--release","8","-classpath",os.pathsep.join(map(str,[jar,legacy])),
             "-d",classes,src/("ShareActivity.java" if share else "ReaderActivity.java")],env)
        run([TOOLS/"d8.bat","--min-api","21","--lib",jar,"--classpath",legacy,"--output",tmp,
             *classes.rglob("*.class")],env)
        for variant in (("file-share",) if share else ("broken","control")):
            manifest=ET.parse(src/"AndroidManifest.xml")
            ET.register_namespace("android","http://schemas.android.com/apk/res/android")
            if share:
                ns="{http://schemas.android.com/apk/res/android}"
                manifest.find("uses-sdk").set(ns+"targetSdkVersion","22")
                manifest.find("application/activity").set(ns+"name",".ShareActivity")
                receiver=ET.SubElement(manifest.find("application/activity"),"intent-filter")
                ET.SubElement(receiver,"action",{ns+"name":"org.ggbond.archivereader.OPEN_RECORD"})
                ET.SubElement(receiver,"category",{ns+"name":"android.intent.category.DEFAULT"})
                ET.SubElement(receiver,"data",{ns+"mimeType":"application/vnd.ggbond.record"})
            if variant=="control":
                ET.SubElement(manifest.find("application"),"uses-library",
                              {"{http://schemas.android.com/apk/res/android}name":"org.apache.http.legacy"})
            manifest.write(tmp/"AndroidManifest.xml",encoding="utf-8")
            run([TOOLS/"aapt2.exe","link","-I",jar,"--manifest","AndroidManifest.xml","-o","base.apk"],cwd=tmp)
            with ZipFile(tmp/"base.apk","a") as z:z.write(tmp/"classes.dex","classes.dex")
            run([TOOLS/"zipalign.exe","-f","4","base.apk","aligned.apk"],cwd=tmp)
            dest=src/f"archive-reader-{variant}.apk"
            run(["java","-jar",TOOLS/"lib/apksigner.jar","sign","--ks",key,"--ks-pass","env:REVIVER_KEYPASS",
                 "--v4-signing-enabled","false","--out",dest,tmp/"aligned.apk"],env)
            print(dest)


if __name__=="__main__":
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file-share",action="store_true",help="Build the legacy file-sharing regression fixture")
    build(parser.parse_args().file_share)
