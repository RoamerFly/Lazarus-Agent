"""File hashing compatible with the Python versions supported by the desktop build."""

from __future__ import annotations

import hashlib
from typing import BinaryIO


def file_digest(stream: BinaryIO, algorithm: str = "sha256", /):
    """Return a hashlib digest without requiring Python 3.11's file_digest API."""
    native = getattr(hashlib, "file_digest", None)
    if native is not None:
        return native(stream, algorithm)
    digest = hashlib.new(algorithm)
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest
