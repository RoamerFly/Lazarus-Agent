from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from updater import (
    UpdateCancelled, UpdateError, download_asset, ignore_version, ignored_version,
    parse_manifest, verify_asset_file, verify_manifest,
)


class FakeResponse:
    status = 200

    def __init__(self, blocks: list[bytes]):
        self.blocks = iter(blocks)
        self.headers = {"Content-Length": str(sum(len(item) for item in blocks))}

    def read(self, _size: int) -> bytes:
        return next(self.blocks, b"")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class UpdaterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.private = Ed25519PrivateKey.generate()
        self.public = self.root / "update-public.pem"
        self.public.write_bytes(self.private.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
        ))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def signed_manifest(self, **changes) -> tuple[bytes, bytes]:
        document = {
            "schema": 1,
            "version": "0.2.1",
            "published_at": "2026-11-03T00:00:00Z",
            "notes": "test",
            "assets": {
                "windows-x64-portable-exe": {
                    "name": "Lazarus-Agent.exe",
                    "url": "https://github.com/RoamerFly/Lazarus-Agent/releases/download/v0.2.1/Lazarus-Agent.exe",
                    "sha256": "a" * 64,
                },
            },
        }
        document.update(changes)
        raw = json.dumps(document).encode("utf-8")
        return raw, base64.b64encode(self.private.sign(raw))

    def test_valid_signature_is_required_before_manifest_is_used(self) -> None:
        raw, signature = self.signed_manifest()
        verify_manifest(raw, signature, self.public)
        self.assertEqual(parse_manifest(raw).version, "0.2.1")
        with self.assertRaises(UpdateError):
            verify_manifest(raw + b" ", signature, self.public)

    def test_manifest_rejects_http_traversal_and_bad_hash(self) -> None:
        raw, _ = self.signed_manifest(assets={
            "windows-x64-portable-exe": {
                "name": "../unsafe.exe", "url": "http://example.test/x.exe", "sha256": "wrong",
            },
        })
        with self.assertRaises(UpdateError):
            parse_manifest(raw)

    def test_manifest_requires_stable_semver(self) -> None:
        raw, _ = self.signed_manifest(version="0.2")
        with self.assertRaises(UpdateError):
            parse_manifest(raw)

    def manifest_for_asset(self, content: bytes):
        raw, _ = self.signed_manifest(assets={
            "windows-x64-portable-exe": {
                "name": "Lazarus-Agent.exe", "url": "https://fixture.invalid/Lazarus-Agent.exe",
                "sha256": hashlib.sha256(content).hexdigest(),
            },
        })
        return parse_manifest(raw)

    def test_signed_fixture_downloads_and_rechecks_before_install(self) -> None:
        content = b"signed-fixture-new-exe"
        manifest = self.manifest_for_asset(content)
        events = []
        with patch("updater.urlopen", return_value=FakeResponse([content[:7], content[7:]])):
            artifact = download_asset(manifest, "windows-x64-portable-exe", destination=self.root, progress=events.append)
        self.assertEqual(artifact.read_bytes(), content)
        self.assertEqual(events[-1].percent, 100)
        verify_asset_file(manifest, "windows-x64-portable-exe", artifact)
        artifact.write_bytes(b"tampered")
        with self.assertRaises(UpdateError):
            verify_asset_file(manifest, "windows-x64-portable-exe", artifact)

    def test_cancelled_fixture_never_publishes_partial_executable(self) -> None:
        content = b"a" * 32
        manifest = self.manifest_for_asset(content)
        cancelled = threading.Event()
        def stop(_event):
            cancelled.set()
        with patch("updater.urlopen", return_value=FakeResponse([content])):
            with self.assertRaises(UpdateCancelled):
                download_asset(manifest, "windows-x64-portable-exe", destination=self.root,
                               cancel_event=cancelled, progress=stop)
        self.assertFalse((self.root / "Lazarus-Agent.exe").exists())
        self.assertFalse((self.root / "Lazarus-Agent.exe.part").exists())

    def test_ignore_is_exact_version_only(self) -> None:
        notice = self.root / "update-notices.json"
        ignore_version("0.2.1", notice)
        self.assertEqual(ignored_version(notice), "0.2.1")
        self.assertNotEqual(ignored_version(notice), "0.2.2")


if __name__ == "__main__":
    unittest.main()
