"""Small, dependency-free native-library inspection helpers for APK archives."""
import re
import struct


# ELF program header and dynamic-table constants.  DT_TEXTREL is obsolete, but
# Android's modern linker deliberately rejects it for apps targeting API 23+.
PT_DYNAMIC = 2
DT_NULL = 0
DT_TEXTREL = 22
DT_FLAGS = 30
DF_TEXTREL = 0x4


def elf_uses_textrel(data):
    """Return whether a complete ELF contains a dynamic TEXTREL declaration.

    Malformed/non-ELF data is deliberately reported as False here: packaging
    inspection must not manufacture a compatibility fact from an unreadable
    library.  Callers retain the member path as the evidence boundary.
    """
    if len(data) < 0x34 or data[:4] != b"\x7fELF" or data[5] not in (1, 2):
        return False
    little = "<" if data[5] == 1 else ">"
    if data[4] == 1:  # ELF32
        if len(data) < 52:
            return False
        phoff = struct.unpack_from(little + "I", data, 28)[0]
        phentsize, phnum = struct.unpack_from(little + "HH", data, 42)
        p_fmt, dyn_fmt = little + "IIIIIIII", little + "II"
    elif data[4] == 2:  # ELF64
        if len(data) < 64:
            return False
        phoff = struct.unpack_from(little + "Q", data, 32)[0]
        phentsize, phnum = struct.unpack_from(little + "HH", data, 54)
        p_fmt, dyn_fmt = little + "IIQQQQQQ", little + "QQ"
    else:
        return False
    if phentsize < struct.calcsize(p_fmt) or phoff + phentsize * phnum > len(data):
        return False
    for index in range(phnum):
        entry = struct.unpack_from(p_fmt, data, phoff + index * phentsize)
        if entry[0] != PT_DYNAMIC:
            continue
        offset, size = (entry[1], entry[4]) if data[4] == 1 else (entry[2], entry[5])
        step = struct.calcsize(dyn_fmt)
        if offset + size > len(data):
            continue
        for pos in range(offset, offset + size - step + 1, step):
            tag, value = struct.unpack_from(dyn_fmt, data, pos)
            if tag == DT_TEXTREL or (tag == DT_FLAGS and value & DF_TEXTREL):
                return True
            if tag == DT_NULL:
                break
    return False


def library_member(name):
    """Return (ABI, library basename) for a normal APK native member."""
    match = re.fullmatch(r"lib/([^/]+)/([^/]+\.so)", name)
    return match.groups() if match else None
