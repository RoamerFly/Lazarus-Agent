"""Obtain Google emulator components for an isolated Windows lab (no global SDK edits)."""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
from zipfile import ZipFile


BASE = "https://dl.google.com/android/repository/"
CACHE = Path(os.environ["LOCALAPPDATA"]) / "LegacyApkReviver"


def fetch_component(component):
    image = component != "emulator"
    prefix = "sys-img/android/" if image else ""
    index = prefix + "sys-img2-1.xml" if image else "repository2-1.xml"
    with urllib.request.urlopen(BASE + index, timeout=30) as response:
        catalog = ET.parse(response)
    package_id = f"system-images;android-{component};default;x86_64" if image else "emulator"
    pkg = next(p for p in catalog.getroot() if p.get("path") == package_id and
               (p.find("channelRef") is None or p.find("channelRef").get("ref") == "channel-0"))
    archive = next(a for a in pkg.findall(".//archive") if a.findtext("host-os") in (None, "windows"))
    name = archive.findtext("complete/url")
    digest = archive.findtext("complete/checksum")
    size = int(archive.findtext("complete/size"))
    out = CACHE / "downloads" / name
    out.parent.mkdir(parents=True, exist_ok=True)
    if not out.exists():
        with tempfile.NamedTemporaryFile(dir=out.parent, suffix=".part", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            with urllib.request.urlopen(BASE.replace("dl.google.com", "redirector.gvt1.com/edgedl") + prefix + name, timeout=90) as src, tmp_path.open("wb") as dst:
                shutil.copyfileobj(src, dst, length=1024*1024)
            with tmp_path.open("rb") as raw:
                if tmp_path.stat().st_size != size or hashlib.file_digest(raw, "sha1").hexdigest() != digest:
                    raise ValueError(f"Google repository checksum mismatch for {name}")
            tmp_path.replace(out)
        finally:
            tmp_path.unlink(missing_ok=True)
    with out.open("rb") as raw:
        if out.stat().st_size != size or hashlib.file_digest(raw, "sha1").hexdigest() != digest:
            raise ValueError(f"Archive does not match official catalog: {out}")
    dest = CACHE / "lab-sdk"
    if image:
        dest = dest / "system-images" / f"android-{component}" / "default"
    dest.mkdir(parents=True, exist_ok=True)
    with ZipFile(out) as z:
        z.extractall(dest)
    print(f"Verified {name} ({digest}); extracted to {dest}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("component", choices=["34", "35", "36", "emulator"])
    fetch_component(parser.parse_args().component)
