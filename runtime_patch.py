"""Append one Android platform shared library declaration to compiled AXML."""
import struct
import re

LIBRARY = "org.apache.http.legacy"
KNOWN_SHARED_LIBRARIES = frozenset({
    "org.apache.http.legacy", "android.test.runner", "android.test.base", "android.test.mock",
})
NO_INDEX = 0xffffffff


def strings_and_append(pool, wanted):
    _, header, _ = struct.unpack_from("<HHI",pool)
    count, styles, flags, start, style_start = struct.unpack_from("<IIIII",pool,8)
    utf8=bool(flags & 0x100)
    offsets=list(struct.unpack_from(f"<{count}I",pool,header))
    values=[]
    for pos in offsets:
        i=start+pos
        if utf8:
            for _ in range(2):
                length=pool[i]; i+=1
                if length & 0x80:
                    length=((length&0x7f)<<8)|pool[i];i+=1
            values.append(pool[i:i+length].decode("utf-8","surrogatepass"))
        else:
            length=struct.unpack_from("<H",pool,i)[0]; i+=2
            if length & 0x8000:
                length=((length&0x7fff)<<16)|struct.unpack_from("<H",pool,i)[0];i+=2
            values.append(pool[i:i+2*length].decode("utf-16le","surrogatepass"))
    old_values=values.copy()
    payload=bytearray(pool[start:style_start or len(pool)])
    for s in wanted:
        if s in values:
            continue
        values.append(s); offsets.append(len(payload))
        if utf8:
            payload.extend(bytes([len(s),len(s)])+s.encode("ascii")+b"\0")
        else:
            payload.extend(struct.pack("<H",len(s))+s.encode("utf-16le")+b"\0\0")
    payload.extend(b"\0"*((-len(payload))%4))
    new_start=start+4*(len(values)-count)
    out=bytearray(pool[:header])
    out.extend(struct.pack(f"<{len(offsets)}I",*offsets))
    out.extend(pool[header+4*count:start])
    out.extend(payload)
    if style_start: out.extend(pool[style_start:])
    struct.pack_into("<I",out,4,len(out))
    struct.pack_into("<IIIII",out,8,len(values),styles,flags & ~1,new_start,
                     new_start+len(payload) if style_start else 0)
    return old_values,values,bytes(out)


def _approved_library(library, discovered_libraries):
    discovered_libraries = tuple(discovered_libraries or ())
    if any(not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,99}", name)
           for name in discovered_libraries):
        raise ValueError("Invalid verified shared-library record")
    return library in KNOWN_SHARED_LIBRARIES or library in discovered_libraries


def declare_shared_library(data, library, discovered_libraries=()):
    """Declare one allow-listed platform library in binary AndroidManifest.xml.

    The caller must have obtained the library name from device evidence.  This
    function deliberately does not accept arbitrary manifest attributes.
    """
    if not _approved_library(library, discovered_libraries):
        raise ValueError("Unsupported platform shared library")
    if struct.unpack_from("<HHI",data)!=(3,8,len(data)):
        raise ValueError("Unsupported binary manifest")
    out=bytearray(data[:8])
    old=values=ids=None
    seen=False
    offset=8
    while offset<len(data):
        kind,hdr,size=struct.unpack_from("<HHI",data,offset)
        if hdr<8 or size<hdr or offset+size>len(data):raise ValueError("Malformed AXML chunk")
        chunk=data[offset:offset+size]
        if kind==1:
            old,values,chunk=strings_and_append(chunk,["uses-library",library])
        elif kind==0x180:
            ids=struct.unpack_from(f"<{(size-hdr)//4}I",chunk,hdr)
        elif kind==0x102:
            element=old[struct.unpack_from("<I",chunk,hdr+4)[0]]
            if element=="uses-library":
                first,step,count=struct.unpack_from("<HHH",chunk,hdr+8)
                for i in range(count):
                    pos=hdr+first+step*i
                    attr=struct.unpack_from("<I",chunk,pos+4)[0]
                    if attr<len(ids) and ids[attr]==0x01010003 and chunk[pos+15]==3:
                        if old[struct.unpack_from("<I",chunk,pos+16)[0]]==library:
                            raise ValueError("Platform shared library already declared")
        elif kind==0x103 and old[struct.unpack_from("<I",chunk,hdr+4)[0]]=="application":
            if seen: raise ValueError("Multiple application nodes")
            elem=values.index("uses-library")
            ns=values.index("http://schemas.android.com/apk/res/android")
            name=ids.index(0x01010003)
            attr=struct.pack("<IIIHBBI",ns,name,NO_INDEX,8,0,3,values.index(library))
            line=struct.unpack_from("<I",chunk,8)[0]
            out+=struct.pack("<HHIIIIIHHHHHH",0x102,16,56,line,NO_INDEX,NO_INDEX,elem,20,20,1,0,0,0)+attr
            out+=struct.pack("<HHIIIII",0x103,16,24,line,NO_INDEX,NO_INDEX,elem)
            seen=True
        out+=chunk
        offset+=size
    if not seen: raise ValueError("Missing application element")
    struct.pack_into("<I",out,4,len(out))
    return bytes(out)


def declare_legacy_http(data):
    """Backward-compatible name for the previously audited HTTP repair."""
    return declare_shared_library(data, LIBRARY)
