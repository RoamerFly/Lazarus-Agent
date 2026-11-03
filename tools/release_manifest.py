"""Create the signed metadata consumed by Lazarus Agent's Windows updater."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization


def asset(value: str) -> tuple[str, Path]:
    try:
        kind, filename = value.split("=", 1)
    except ValueError as error:
        raise argparse.ArgumentTypeError("资产格式为 kind=path") from error
    path = Path(filename).resolve()
    if not kind or not path.is_file():
        raise argparse.ArgumentTypeError("资产类型为空或文件不存在")
    return kind, path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while block := file.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--published-at", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--notes", default="")
    parser.add_argument("--asset", action="append", type=asset, required=True)
    args = parser.parse_args()
    private_key = serialization.load_pem_private_key(args.private_key.read_bytes(), password=None)
    assets = {
        kind: {"name": path.name, "url": f"{args.base_url.rstrip('/')}/{path.name}", "sha256": sha256(path)}
        for kind, path in args.asset
    }
    payload = {
        "schema": 1,
        "version": args.version,
        "published_at": args.published_at,
        "notes": args.notes,
        "assets": assets,
    }
    # Stable pretty JSON makes audit/reproducible verification possible; do not
    # reformat latest.json after this point or its detached signature changes.
    manifest = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    args.out.mkdir(parents=True, exist_ok=True)
    manifest_file = args.out / "latest.json"
    manifest_file.write_bytes(manifest)
    signature = private_key.sign(manifest)
    (args.out / "latest.json.sig").write_bytes(base64.b64encode(signature) + b"\n")


if __name__ == "__main__":
    main()
