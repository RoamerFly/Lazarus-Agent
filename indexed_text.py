"""Length-prefixed Java readUTF text pools with a big-endian (id, offset) index."""

from dataclasses import dataclass
from pathlib import Path
import re
import struct
import zlib

# Only inspect files selected by a source-language directory, then require
# complete structural agreement before treating any binary data as user text.
ENGLISH = re.compile(r"en(?:[-_](?:us|gb|au|ca))?|english", re.I)


def read_mutf(raw):
    if b"\x00" in raw or any(b >= 0xF0 for b in raw):
        raise ValueError("Invalid modified UTF-8")
    units = raw.replace(b"\xc0\x80", b"\x00").decode("utf8", "surrogatepass")
    return units.encode("utf-16-be", "surrogatepass").decode("utf-16-be")


def write_mutf(value):
    units = value.encode("utf-16-be")
    data = "".join(chr(unit) for (unit,) in struct.iter_unpack(">H", units))
    return data.encode("utf8", "surrogatepass").replace(b"\x00", b"\xc0\x80")


@dataclass
class TextPool:
    header: bytes
    rows: list

    @classmethod
    def parse(cls, data):
        if len(data) < 24:
            return None
        version, count, index, pool = struct.unpack_from(">4I", data)
        if not (
            0 < count <= 65536 and 16 <= index <= 4096 and pool == index + count * 8 and pool < len(data)
        ):
            return None
        if any(data[16:index]):
            return None
        rows = []
        cursor = pool
        previous = -1
        for key, offset in struct.iter_unpack(">II", data[index:pool]):
            if key <= previous or cursor != pool + offset or cursor + 2 > len(data):
                return None
            size = struct.unpack_from(">H", data, cursor)[0]
            end = cursor + 2 + size
            if end > len(data):
                return None
            try:
                value = read_mutf(data[cursor + 2 : end])
            except (ValueError, UnicodeError):
                return None
            rows.append((key, value))
            cursor = end
            previous = key
        return cls(data[:index], rows) if cursor == len(data) else None

    def translated(self, mapping):
        entries = bytearray()
        payload = bytearray()
        expected = []
        for key, value in self.rows:
            value = mapping.get(value, value)
            data = write_mutf(value)
            if len(data) > 65535:
                raise ValueError("译文超过 Java readUTF 的单条长度限制")
            entries.extend(struct.pack(">II", key, len(payload)))
            payload.extend(struct.pack(">H", len(data)) + data)
            expected.append((key, value))
        header = bytearray(self.header)
        # Readers may cache by this first integer. Different translations of
        # the same original need different versions even on a second install.
        old_version = struct.unpack_from(">I", header)[0]
        version = zlib.crc32(payload) & 0x7FFFFFFF
        struct.pack_into(">I", header, 0, version if version != old_version else version ^ 1)
        result = bytes(header + entries + payload)
        check = self.parse(result)
        if check is None or check.rows != expected:
            raise ValueError("游戏文本表回读校验失败")
        return result


def discover(assets):
    result = {}
    scanned = 0
    for p in assets.rglob("*"):
        if not p.is_file() or not any(ENGLISH.fullmatch(part) for part in p.relative_to(assets).parts[:-1]):
            continue
        size = p.stat().st_size
        if not 24 <= size <= 8 * 1024**2:
            continue
        scanned += size
        if scanned > 128 * 1024**2:
            raise ValueError("英文语言目录超过扫描上限")
        parsed = TextPool.parse(p.read_bytes())
        if parsed and any(re.search("[a-zA-Z]", v) for _, v in parsed.rows):
            result[p] = parsed
    return result
