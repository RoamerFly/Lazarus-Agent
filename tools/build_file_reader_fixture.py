"""Build the team-owned APK used by the remembered-record behaviour test."""
import argparse
import os
import shutil
from pathlib import Path
import sys
import tempfile
from xml.etree import ElementTree as ET
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bench_device import SDK, TOOLS
from offline_kit import java_environment
from reviver import run, test_key


def _platform_jar():
    candidates = sorted(SDK.glob("platforms/android-*/android.jar"), reverse=True)
    return next((candidate for candidate in candidates if candidate.is_file()), None)


VARIANTS = {
    "pocket-list": ("pocket-list", "MainActivity.java", "pocket-list-en.apk"),
    "tea-timer": ("tea-timer", "MainActivity.java", "tea-timer-en.apk"),
    "reader": ("file-reader", "FileReaderActivity.java", "file-reader-forgets-selection.apk"),
    "vault": ("file-reader-variant", "ChoiceVaultActivity.java", "memo-vault-forgets-selection.apk"),
}


def build(variant: str = "reader", output: Path | None = None) -> Path:
    """Create the fixture from the checked-in Java source and manifest."""
    android_jar = _platform_jar()
    if not android_jar:
        raise RuntimeError("需要 Android SDK platform android.jar 才能构建行为样本")
    if variant not in VARIANTS:
        raise ValueError("未知行为样本：" + variant)
    folder, java_source, filename = VARIANTS[variant]
    source = ROOT / "fixtures" / folder
    output = output or source / filename
    if output.exists():
        output.unlink()
    environment = java_environment(os.environ.copy())
    key = test_key(environment)
    with tempfile.TemporaryDirectory(dir=ROOT / "build") as temporary:
        work = Path(temporary)
        classes = work / "classes"
        classes.mkdir()
        manifest = ET.parse(source / "AndroidManifest.xml")
        manifest.write(work / "AndroidManifest.xml", encoding="utf-8", xml_declaration=True)
        resource_args = []
        if (source / "res").is_dir():
            shutil.copytree(source / "res", work / "res")
            run([TOOLS / "aapt2.exe", "compile", "--dir", "res", "-o", "res.zip"], cwd=work)
            resource_args = ["--java", ".", "res.zip"]
        run([TOOLS / "aapt2.exe", "link", "-I", android_jar, "--manifest", "AndroidManifest.xml",
             "-o", "base.apk", *resource_args], cwd=work)
        run([
            "javac", "-encoding", "UTF-8", "-source", "8", "-target", "8", "-classpath", android_jar,
            "-d", classes, source / java_source, *work.rglob("R.java"),
        ], environment)
        run([
            TOOLS / "d8.bat", "--min-api", "21", "--lib", android_jar, "--output", work,
            *classes.rglob("*.class"),
        ], environment)
        with ZipFile(work / "base.apk", "a") as archive:
            archive.write(work / "classes.dex", "classes.dex")
            for record in sorted((source / "assets").glob("*.txt")):
                archive.write(record, "assets/" + record.name)
        run([TOOLS / "zipalign.exe", "-f", "4", "base.apk", "aligned.apk"], cwd=work)
        run([
            "java", "-jar", TOOLS / "lib" / "apksigner.jar", "sign", "--ks", key,
            "--ks-pass", "env:REVIVER_KEYPASS", "--v4-signing-enabled", "false",
            "--out", output, work / "aligned.apk",
        ], environment)
        run(["java", "-jar", TOOLS / "lib" / "apksigner.jar", "verify", "--verbose", output], environment)
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", choices=sorted(VARIANTS), default="reader")
    print(build(parser.parse_args().variant))
