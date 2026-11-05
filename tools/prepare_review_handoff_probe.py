"""Transport the allowlisted r2 replay files byte-for-byte to a fresh Windows runner."""
import argparse
import base64
from hashlib import sha256
from io import BytesIO
import gzip
import json
import os
from pathlib import Path
import tarfile
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
RECEIPT = ROOT / "docs/validation/0.6.16-r2-handoff-inputs.json"


def verify(data, expected):
    if len(data) != expected["bytes"] or sha256(data).hexdigest() != expected["sha256"]:
        raise ValueError("Review replay file differs from the delivered r2 archive")


def pack(path, expected):
    receipt = json.loads(RECEIPT.read_bytes())
    if sha256(path.read_bytes()).hexdigest() != receipt["review_package_sha256"]:
        raise ValueError("Review archive hash differs from the delivered r2 archive")
    payload = BytesIO()
    with ZipFile(path) as source, tarfile.open(fileobj=payload, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for name, spec in expected.items():
            data = source.read(name)
            verify(data, spec)
            member = tarfile.TarInfo(name)
            member.size = len(data)
            tar.addfile(member, BytesIO(data))
    return base64.b64encode(gzip.compress(payload.getvalue(), mtime=0)).decode("ascii")


def restore(event, output, expected):
    encoded = json.loads(event.read_bytes())["inputs"]["review_input"]
    if not encoded or len(encoded) > 65_000:
        raise ValueError("Missing or oversized review input")
    approved = {}
    with tarfile.open(fileobj=BytesIO(base64.b64decode(encoded, validate=True)), mode="r:gz") as tar:
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
    operation.add_argument("--pack", type=Path, help="Complete r2 review ZIP")
    operation.add_argument("--restore", type=Path, help="New replay directory for the GitHub runner")
    args = parser.parse_args()
    expected = json.loads(RECEIPT.read_bytes())["files"]
    if args.pack:
        print(json.dumps({"version": "0.6.16", "review_input": pack(args.pack, expected)}))
    else:
        restore(Path(os.environ["GITHUB_EVENT_PATH"]), args.restore, expected)
