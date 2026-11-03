"""Position-independent CRT fixups for three byte-identified ARMv7 engine builds."""
import hashlib
import struct


# (ARM literal VA, Thumb literal VA, ARM __cxa_atexit PLT entry VA).
# PLT entries were resolved via the __cxa_atexit JUMP_SLOT, not by proximity.
PROFILES = {
    "aa6d178d84881b15ed7047636b61f26546f1490797267ef9271e07619281b5d4": (0x3144, 0x315c, 0x30a4),
    "37608588bca57f1eb79ff0d26319f4a8c7dc32b7f196295c554ededd1758609a": (0x15ab4, None, None),
    "096753a1976ddccbfb25fb52979ff40347167a95434dd33867063cd9b9c1e803": (0x68a7c, 0x68a94, 0x68134),
}


def rewrite(data):
    profile = PROFILES.get(hashlib.sha256(data).hexdigest())
    if profile is None:
        raise ValueError("Unreviewed ARM engine build")
    literal, thumb_literal, plt = profile
    b = bytearray(data)
    def word(i):
        return struct.unpack_from("<I", b, i)[0]
    def put(i, value):
        struct.pack_into("<I", b, i, value)
    phoff = word(28)
    entsize, count = struct.unpack_from("<HH", b, 42)
    headers = [struct.unpack_from("<8I", b, phoff+i*entsize) for i in range(count)]
    dyn = next(p for p in headers if p[0] == 2)
    entries = list(struct.iter_unpack("<II", data[dyn[1]:dyn[1]+dyn[4]]))
    tags = dict(entries)
    def offset(va):
        seg = next(p for p in headers if p[0]==1 and p[2]<=va<p[2]+p[4])
        return va-seg[2]+seg[1]

    # Old ARM: address of literal -> dereference relocated absolute address.
    # New ARM: read relative delta -> add runtime PC (equals literal VA).
    at = offset(literal)
    if word(at-12)!=0xe28f0004 or word(at-8)!=0xe5900000:
        raise ValueError("Unexpected ARM CRT instructions")
    put(at-12,0xe59f0004)
    put(at-8,0xe08f0000)
    put(at,(word(at)-literal)&0xffffffff)
    if thumb_literal is not None:
        at = offset(thumb_literal)
        if b[at-10:at-6]!=bytes.fromhex("024b9847"):
            raise ValueError("Unexpected Thumb CRT call")
        # BLX immediate uses Align(PC,4). Both pinned stubs are within 4 KiB.
        delta = plt - ((thumb_literal-10+4)&~3)
        if not -4096<=delta<0 or delta%4:
            raise ValueError("Unexpected PLT displacement")
        struct.pack_into("<HH",b,at-10,0xf7ff,0xe800|((delta>>1)&0x7fe))
        put(at,0)

    # Drop exactly these relocations and keep the relative relocation prefix valid.
    rel = offset(tags[17])
    records = list(struct.iter_unpack("<II",data[rel:rel+tags[18]]))
    removed = {literal:23}
    if thumb_literal is not None:
        removed[thumb_literal]=2
    if {va:info&255 for va,info in records if va in removed}!=removed:
        raise ValueError("Unexpected CRT relocations")
    kept = b"".join(struct.pack("<II",va,info) for va,info in records if va not in removed)
    b[rel:rel+tags[18]]=kept.ljust(tags[18],b"\0")
    shoff=word(32)
    shsize,shcount=struct.unpack_from("<HH",b,46)
    for i in range(shcount):
        sh=shoff+i*shsize
        if word(sh+4)==9 and word(sh+16)==rel:
            put(sh+20,len(kept))
    updates={18:len(kept),0x6ffffffa:tags[0x6ffffffa]-1}
    packed=b"".join(struct.pack("<II",tag,updates.get(tag,val)) for tag,val in entries if tag!=22)
    b[dyn[1]:dyn[1]+dyn[4]]=packed.ljust(dyn[4],b"\0")
    return bytes(b)
