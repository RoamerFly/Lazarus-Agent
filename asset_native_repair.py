"""Strict discovery of in-APK native payload replacements.

This module intentionally does *not* attempt to repair arbitrary native
libraries.  It recognises only a narrow packaging convention used by some old
Unity channel builds: a small library whose *SONAME* is ``lib*_proxy.so`` at
the normal APK library path, and a complete, same-name payload at
``assets/libs``.  A caller receives paths,
not bytes, so it can keep its own archive-write and signing boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import struct
from typing import Iterable
from zipfile import BadZipFile, ZipFile


_MIN_LIBRARY_BYTES = 4 * 1024
_MAX_LIBRARY_BYTES = 64 * 1024 * 1024
_PT_LOAD = 1
_PT_DYNAMIC = 2
_SHN_UNDEF = 0
_STB_GLOBAL = 1
_STB_WEAK = 2

# Android ABI directory names are part of the evidence.  Do not infer an ABI
# from a filename or allow an otherwise-valid ELF to cross an ABI directory.
_ANDROID_ABI_ELF = {
    "armeabi": (1, 40),
    "armeabi-v7a": (1, 40),
    "arm64-v8a": (2, 183),
    "x86": (1, 3),
    "x86_64": (2, 62),
    "riscv64": (2, 243),
}


@dataclass(frozen=True)
class _ElfInfo:
    elf_class: int
    byte_order: str
    elf_type: int
    machine: int
    soname: str
    exports: frozenset[str]


def _u(data: bytes, offset: int, fmt: str):
    size = struct.calcsize(fmt)
    if offset < 0 or offset + size > len(data):
        raise ValueError("truncated ELF")
    return struct.unpack_from(fmt, data, offset)


def _cstring(data: bytes, offset: int, limit: int) -> str:
    if offset < 0 or offset >= limit or limit > len(data):
        raise ValueError("invalid ELF string offset")
    end = data.find(b"\0", offset, limit)
    if end < 0:
        raise ValueError("unterminated ELF string")
    # SONAMEs and exported symbol names are ASCII identifiers in this use case.
    return data[offset:end].decode("ascii", "strict")


def _elf_info(data: bytes) -> _ElfInfo | None:
    """Parse the small, security-relevant subset required for a replacement.

    ``None`` means that the member is not a complete dynamically linked ELF.
    There is deliberately no best-effort parsing: ambiguous or truncated
    members must not become automatic repair candidates.
    """
    try:
        if not (_MIN_LIBRARY_BYTES <= len(data) <= _MAX_LIBRARY_BYTES):
            return None
        if data[:4] != b"\x7fELF" or len(data) < 16 or data[6] != 1:
            return None
        elf_class, endian = data[4], data[5]
        if elf_class not in (1, 2) or endian not in (1, 2):
            return None
        order = "<" if endian == 1 else ">"
        if elf_class == 1:
            if len(data) < 52:
                return None
            elf_type, machine = _u(data, 16, order + "HH")
            phoff = _u(data, 28, order + "I")[0]
            shoff = _u(data, 32, order + "I")[0]
            phentsize, phnum = _u(data, 42, order + "HH")
            shentsize, shnum = _u(data, 46, order + "HH")
            phfmt, symfmt = order + "IIIIIIII", order + "IIIBBH"
            phmin, symentry = 32, 16
        else:
            if len(data) < 64:
                return None
            elf_type, machine = _u(data, 16, order + "HH")
            phoff = _u(data, 32, order + "Q")[0]
            shoff = _u(data, 40, order + "Q")[0]
            phentsize, phnum = _u(data, 54, order + "HH")
            shentsize, shnum = _u(data, 58, order + "HH")
            phfmt, symfmt = order + "IIQQQQQQ", order + "IBBHQQ"
            phmin, symentry = 56, 24
        # Shared objects only.  ET_EXEC/relocatable objects are never a safe
        # drop-in for lib/<abi> on Android.
        if elf_type != 3 or machine == 0:
            return None
        if phnum == 0 or phentsize < phmin or phoff + phentsize * phnum > len(data):
            return None
        load_segments = []
        dynamic_segments = []
        for index in range(phnum):
            values = _u(data, phoff + index * phentsize, phfmt)
            if elf_class == 1:
                file_offset, vaddr, file_size, mem_size = values[1], values[2], values[4], values[5]
            else:
                file_offset, vaddr, file_size, mem_size = values[2], values[3], values[5], values[6]
            if values[0] == _PT_LOAD:
                if file_offset + file_size > len(data) or mem_size < file_size:
                    return None
                load_segments.append((vaddr, file_offset, file_size))
            elif values[0] == _PT_DYNAMIC:
                if file_offset + file_size > len(data):
                    return None
                dynamic_segments.append((file_offset, file_size))
        if not load_segments:
            return None
        if len(dynamic_segments) != 1:
            return None

        def vaddr_to_offset(address: int) -> int:
            for segment_vaddr, segment_offset, segment_size in load_segments:
                if segment_vaddr <= address < segment_vaddr + segment_size:
                    return segment_offset + address - segment_vaddr
            raise ValueError("dynamic address outside PT_LOAD")

        dynamic_entry = 8 if elf_class == 1 else 16
        dynoff, dynsize = dynamic_segments[0]
        if dynsize == 0 or dynsize % dynamic_entry:
            return None
        tags: dict[int, list[int]] = {}
        for pos in range(dynoff, dynoff + dynsize, dynamic_entry):
            tag, value = _u(data, pos, order + ("II" if elf_class == 1 else "QQ"))
            tags.setdefault(tag, []).append(value)
            if tag == 0:  # DT_NULL
                break
        required = (4, 5, 6, 10, 11, 14)  # HASH, STRTAB, SYMTAB, STRSZ, SYMENT, SONAME
        if any(len(tags.get(tag, [])) != 1 for tag in required):
            return None
        if tags[11][0] != symentry or tags[10][0] == 0:
            return None
        stroff = vaddr_to_offset(tags[5][0])
        strsize = tags[10][0]
        if stroff + strsize > len(data):
            return None
        soname_candidates = {_cstring(data, stroff + tags[14][0], stroff + strsize)}
        if len(soname_candidates) != 1:
            return None
        hash_offset = vaddr_to_offset(tags[4][0])
        _, symbol_count = _u(data, hash_offset, order + "II")
        if symbol_count == 0 or symbol_count > len(data) // symentry:
            return None
        symoff = vaddr_to_offset(tags[6][0])
        if symoff + symbol_count * symentry > len(data):
            return None
        exports: set[str] = set()
        for pos in range(symoff, symoff + symbol_count * symentry, symentry):
            if elf_class == 1:
                name_index, _, _, info, _, section_index = _u(data, pos, symfmt)
            else:
                name_index, info, _, section_index, _, _ = _u(data, pos, symfmt)
            binding = info >> 4
            if name_index and binding in (_STB_GLOBAL, _STB_WEAK) and section_index != _SHN_UNDEF:
                name = _cstring(data, stroff + name_index, stroff + strsize)
                if name:
                    exports.add(name)
        if not exports:
            return None
        return _ElfInfo(elf_class, order, elf_type, machine, next(iter(soname_candidates)), frozenset(exports))
    except (IndexError, OverflowError, UnicodeDecodeError, ValueError, struct.error):
        return None


def _payload_basename(library: str) -> str | None:
    """Return the only permitted asset basename for a top-level member.

    The member filename is deliberately retained.  The historical packaging
    convention identifies the small proxy through its SONAME, not by renaming
    the APK member itself.
    """
    if not library.startswith("lib") or not library.endswith(".so"):
        return None
    return library


def _valid_top_soname(library: str, soname: str) -> bool:
    """Accept exact SONAME or the audited ``_proxy`` SONAME convention."""
    return soname == library or soname == library[:-3] + "_proxy.so"


def _symbol_match(top: _ElfInfo, payload: _ElfInfo) -> bool:
    shared = top.exports & payload.exports
    # A common JNI_OnLoad alone is not identifying evidence.  Three exports is
    # a conservative lower bound for a proxy and rejects arbitrary ELFs that
    # happen to share runtime glue symbols.  Do not require a percentage: a
    # deliberately small proxy normally exports only a subset of its large
    # runtime payload's public surface.
    return len(shared) >= 3


def replacement_map(source: str | Path) -> dict[str, str]:
    """Return audited ``lib member -> assets/libs member`` replacements.

    Any malformed archive, duplicate entry, invalid ELF, ambiguous candidate,
    or non-matching compatibility property produces ``{}`` rather than a
    partial answer.  This fail-closed result is important because the caller
    may subsequently overwrite a runnable native library.
    """
    try:
        with ZipFile(source) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                return {}
            members = {info.filename: info for info in infos}
            top_members = [name for name in names if name.count("/") == 2 and name.startswith("lib/") and name.endswith(".so")]
            if not top_members:
                return {}
            mappings: dict[str, str] = {}
            for top_name in top_members:
                parts = top_name.split("/")
                if len(parts) != 3 or not parts[1] or not parts[2]:
                    return {}
                abi, library = parts[1], parts[2]
                expected_elf = _ANDROID_ABI_ELF.get(abi)
                if expected_elf is None:
                    return {}
                payload_library = _payload_basename(library)
                if payload_library is None:
                    continue
                payload_name = f"assets/libs/{abi}/{payload_library}"
                if payload_name not in names:
                    continue
                # Check archive metadata before decompression.  This limits
                # memory exposure to this narrow inspection path and rejects
                # oversized members even when their compressed size is small.
                if not (_MIN_LIBRARY_BYTES <= members[top_name].file_size <= _MAX_LIBRARY_BYTES and
                        _MIN_LIBRARY_BYTES <= members[payload_name].file_size <= _MAX_LIBRARY_BYTES):
                    return {}
                top_data, payload_data = archive.read(top_name), archive.read(payload_name)
                if top_data == payload_data:
                    return {}
                top, payload = _elf_info(top_data), _elf_info(payload_data)
                if top is None or payload is None:
                    return {}
                if (top.elf_class != payload.elf_class or top.byte_order != payload.byte_order or
                        top.elf_type != payload.elf_type or top.machine != payload.machine):
                    return {}
                if (top.elf_class, top.machine) != expected_elf:
                    return {}
                # A proxy is only accepted when its SONAME is either the APK
                # filename or that exact filename with ``_proxy`` inserted
                # before .so.  The payload must retain the actual filename as
                # its SONAME.  This rejects unrelated same-ABI .so assets.
                if not _valid_top_soname(library, top.soname) or payload.soname != payload_library:
                    return {}
                if not _symbol_match(top, payload):
                    return {}
                # This NUL-terminated pathname is direct evidence that the
                # proxy was packaged to load this exact embedded payload, not
                # merely another same-name .so found by archive scanning.
                try:
                    direct_reference = payload_name.encode("ascii") + b"\0"
                except UnicodeEncodeError:
                    return {}
                if direct_reference not in top_data:
                    return {}
                mappings[top_name] = payload_name
            return mappings
    except (BadZipFile, OSError, KeyError, RuntimeError):
        return {}
