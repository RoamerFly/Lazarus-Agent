"""Obtain Google emulator components for an isolated Windows lab (no global SDK edits)."""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import sys
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
from zipfile import ZipFile


BASE = "https://dl.google.com/android/repository/"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from storage_paths import resolve_storage_paths  # noqa: E402


# This is a development-only downloader.  The shipped offline kit owns its
# installation flow; use the same data root so a developer does not create a
# second, hidden SDK cache under the old application name.
CACHE = resolve_storage_paths().runtime("android-lab")
MAX_ARCHIVE_MEMBERS = 100_000
MAX_UNCOMPRESSED_BYTES = 12 * 1024 * 1024 * 1024


def _verify_archive(path: Path, expected_size: int, expected_digest: str) -> None:
    with path.open("rb") as raw:
        actual_digest = hashlib.file_digest(raw, "sha1").hexdigest()
    if path.stat().st_size != expected_size or actual_digest != expected_digest:
        raise ValueError(f"Archive does not match official catalog: {path}")


def _safe_extract(archive: Path, destination: Path) -> None:
    """Extract a ZIP only after rejecting traversal, links, and zip bombs."""
    destination = destination.resolve(strict=False)
    with ZipFile(archive) as bundle:
        entries = bundle.infolist()
        if len(entries) > MAX_ARCHIVE_MEMBERS:
            raise ValueError("Archive contains too many entries")
        if sum(entry.file_size for entry in entries) > MAX_UNCOMPRESSED_BYTES:
            raise ValueError("Archive uncompressed size exceeds lab safety limit")
        for entry in entries:
            # A symlink can make a lexically safe later entry escape destination.
            unix_type = (entry.external_attr >> 16) & 0o170000
            if unix_type == 0o120000:
                raise ValueError(f"Refusing symbolic link in archive: {entry.filename}")
            target = (destination / entry.filename).resolve(strict=False)
            if target != destination and destination not in target.parents:
                raise ValueError(f"Refusing path outside destination: {entry.filename}")
        bundle.extractall(destination, entries)


def _extract_without_overwrite(archive: Path, destination: Path) -> None:
    """Stage on the destination volume and publish only a complete extraction."""
    if destination.exists():
        raise FileExistsError(
            f"Refusing to overwrite existing component: {destination}. "
            "Remove it manually only after confirming it is not in use."
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    # NamedTemporaryFile/TemporaryDirectory under destination.parent guarantees
    # that the final rename stays on one volume and is atomic on Windows.
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix=".extract-") as temporary:
        staged = Path(temporary) / "payload"
        staged.mkdir()
        _safe_extract(archive, staged)
        if not any(staged.iterdir()):
            raise ValueError("Archive extraction produced no files")
        # Do not use shutil.move: it may degrade to copy+delete across volumes.
        staged.replace(destination)


def fetch_component(component: str) -> Path:
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
            _verify_archive(tmp_path, size, digest)
            tmp_path.replace(out)
        finally:
            tmp_path.unlink(missing_ok=True)
    _verify_archive(out, size, digest)
    dest = CACHE / "lab-sdk"
    if image:
        dest = dest / "system-images" / f"android-{component}" / "default"
    _extract_without_overwrite(out, dest)
    print(f"Verified {name} ({digest}); extracted to {dest}", flush=True)
    return dest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("component", choices=["34", "35", "36", "emulator"])
    fetch_component(parser.parse_args().component)
