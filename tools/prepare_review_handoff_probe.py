"""Transport the allowlisted review files byte-for-byte to a fresh Windows runner."""
import argparse
import base64
from hashlib import sha256
from io import BytesIO
import gzip
import json
import lzma
import os
from pathlib import Path
import re
import shutil
import tarfile
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
LEGACY_VERSION = "0.6.16"
MAX_INPUT = 65_000


def receipt_path(version):
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("Invalid review release version")
    label = f"{version}-r2" if version == LEGACY_VERSION else version
    return ROOT / "docs/validation" / f"{label}-handoff-inputs.json"


def verify(data, expected):
    if len(data) != expected["bytes"] or sha256(data).hexdigest() != expected["sha256"]:
        raise ValueError("Review replay file differs from the delivered archive")


def pack(path, receipt):
    if sha256(path.read_bytes()).hexdigest() != receipt["review_package_sha256"]:
        raise ValueError("Review archive hash differs from the delivered archive")
    payload = BytesIO()
    with ZipFile(path) as source, tarfile.open(fileobj=payload, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for name, spec in receipt["files"].items():
            data = source.read(name)
            verify(data, spec)
            member = tarfile.TarInfo(name)
            member.size = len(data)
            tar.addfile(member, BytesIO(data))
    raw = payload.getvalue()
    encoded = base64.b64encode(gzip.compress(raw, mtime=0))
    if len(encoded) > MAX_INPUT:
        encoded = base64.b64encode(lzma.compress(raw))
    if len(encoded) > MAX_INPUT:
        raise ValueError("Review replay inputs exceed the workflow dispatch size limit")
    return encoded.decode("ascii")


def restore(event, output, expected):
    encoded = json.loads(event.read_bytes())["inputs"]["review_input"]
    if not encoded or len(encoded) > MAX_INPUT:
        raise ValueError("Missing or oversized review input")
    approved = {}
    with tarfile.open(fileobj=BytesIO(base64.b64decode(encoded, validate=True)), mode="r:*") as tar:
        for member in tar:
            name = member.name
            if name not in expected or name in approved or not member.isfile() or member.size != expected[name]["bytes"]:
                raise ValueError("Review input contains an unexpected entry")
            data = tar.extractfile(member).read()
            verify(data, expected[name])
            approved[name] = data
    if approved.keys() != expected.keys():
        raise ValueError("Required review replay files are missing")
    output.mkdir()
    for name, data in approved.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument("--pack", type=Path, help="Complete review ZIP")
    operation.add_argument("--restore", type=Path, help="New replay directory for the GitHub runner")
    parser.add_argument("--version", default=LEGACY_VERSION)
    args = parser.parse_args()
    receipt_file = receipt_path(args.version)
    receipt = json.loads(receipt_file.read_bytes())
    if args.pack:
        print(json.dumps({"version": args.version, "review_input": pack(args.pack, receipt)}))
    else:
        restore(Path(os.environ["GITHUB_EVENT_PATH"]), args.restore, receipt["files"])
        shutil.copyfile(receipt_file, args.restore / "review-inputs.json")
