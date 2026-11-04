from pathlib import Path
import struct
import tempfile
import unittest
from indexed_text import TextPool, discover, read_mutf, write_mutf


def make_pool():
    strings = ["Go", "Save", "Again"]
    payload = bytearray()
    index = bytearray()
    for key, text in zip((1, 42, 105), strings):
        data = write_mutf(text)
        index.extend(struct.pack(">II", key, len(payload)))
        payload.extend(struct.pack(">H", len(data)) + data)
    return struct.pack(">4I", 5, len(strings), 32, 32 + len(index)) + bytes(16) + index + payload


class IndexedLocalizationTests(unittest.TestCase):
    def test_discovers_renamed_table_and_rebuilds_offsets_and_cache_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "locales/en-GB"
            directory.mkdir(parents=True)
            path = directory / "arbitrary.pack"
            path.write_bytes(make_pool())
            other = directory.parent / "de"
            other.mkdir()
            (other / "arbitrary.pack").write_bytes(make_pool())
            found = discover(Path(tmp))
            self.assertEqual(set(found), {path})
            data = found[path].translated({"Go": "出发吧", "Again": "再来一次"})
            self.assertNotEqual(data[:4], make_pool()[:4])
            self.assertEqual(TextPool.parse(data).rows, [(1, "出发吧"), (42, "Save"), (105, "再来一次")])
            self.assertNotEqual(data[:4], found[path].translated({"Go": "开始"})[:4])

    def test_rejects_corrupt_index_and_unknown_header_extensions(self):
        raw = bytearray(make_pool())
        raw[47] = 0
        self.assertIsNone(TextPool.parse(raw))
        raw = bytearray(make_pool())
        raw[16] = 1
        self.assertIsNone(TextPool.parse(raw))
        self.assertIsNone(TextPool.parse(make_pool()[:-1]))

    def test_java_modified_utf8_roundtrips_nul_and_supplementary_character(self):
        data = bytes.fromhex("c080 eda0bd edb880")
        self.assertEqual(read_mutf(data), "\0😀")
        self.assertEqual(write_mutf("\0😀"), data)
        with self.assertRaises(ValueError):
            read_mutf(b"\0")
