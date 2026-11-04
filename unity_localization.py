"""Discover English NGUI text tables and linked bitmap fonts in Unity 4 serialized assets."""

from collections import defaultdict
import hashlib
import math
import os
from pathlib import Path
import re
import shutil
import struct
import tempfile
from zipfile import ZipFile

from auto_translation import translations

GROUP = re.compile(r"(.*/(?:sharedassets\d+|resources)\.assets)(?:\.split(\d+))?$", re.I)
LINE = re.compile(r"(?m)^([\w.&-]+)[ \t]*=[ \t]*([^\r\n]*)")
MAX_FILE = 96 * 1024**2


def uint(raw, pos):
    return struct.unpack_from("<I", raw, pos)[0]


def groups(names):
    grouped = defaultdict(dict)
    for name in names:
        match = GROUP.fullmatch(name)
        if match:
            index = int(match[2]) if match[2] is not None else -1
            if index in grouped[match[1]]:
                raise ValueError("重复的 Unity 资源分片")
            grouped[match[1]][index] = name
    for base, parts in grouped.items():
        if sorted(parts) not in ([-1], list(range(len(parts)))):
            raise ValueError("Unity 资源分片缺失或混用")
        yield base, [parts[i] for i in sorted(parts)]


def load_asset(data):
    import UnityPy

    if (
        len(data) < 32
        or int.from_bytes(data[8:12], "big") != 9
        or data[16]
        or not data[20:].startswith(b"4.")
    ):
        return None
    return next(iter(UnityPy.load(data).files.values()))


def table(obj):
    raw = obj.get_raw_data()
    size = uint(raw, 0)
    if raw[4 : 4 + size].lower() not in (b"english", b"en", b"en-us", b"en_us"):
        return None
    pos = 4 + ((size + 3) & ~3)
    length = uint(raw, pos)
    if length > 2 * 1024**2 or pos + 4 + length > len(raw):
        return None
    text = raw[pos + 4 : pos + 4 + length].decode("utf-8-sig")
    if len(LINE.findall(text)) < 2:
        return None
    return pos, length, text


def bitmap_font(asset, obj):
    raw = obj.get_raw_data()
    if len(raw) < 128:
        return None
    size, baseline, width, height, name_len = struct.unpack_from("<5I", raw, 48)
    if not (
        12 <= size <= 128
        and 0 < baseline <= size * 2
        and width in (128, 256, 512, 1024, 2048)
        and height in (128, 256, 512, 1024, 2048)
        and 0 < name_len <= 128
    ):
        return None
    pos = 68 + ((name_len + 3) & ~3)
    count = uint(raw, pos)
    if not 10 <= count <= 20000:
        return None
    ids = set()
    end = pos + 4
    for _ in range(count):
        if end + 40 > len(raw):
            return None
        code, x, y, w, h, _, _, _, _, kern = struct.unpack_from("<10i", raw, end)
        if code in ids or min(x, y, w, h, kern) < 0 or x + w > width or y + h > height:
            return None
        ids.add(code)
        end += 40 + 4 * kern
    if len(raw) - end != 52 or not set(map(ord, "AZaz09")) <= ids:
        return None
    material = asset.objects.get(uint(raw, 28))
    if uint(raw, 24) or not material or material.type.name != "Material":
        raise ValueError("NGUI 字库使用外部材质，当前不能安全定位图集")
    maps = [v.m_Texture for k, v in material.read().m_SavedProperties.m_TexEnvs if k.name == "_MainTex"]
    if len(maps) != 1 or maps[0].m_FileID or maps[0].m_PathID not in asset.objects:
        raise ValueError("NGUI 材质引用的图集不在同一资源内")
    return (pos, end, width, height, size, ids, maps[0].m_PathID)


def extend_atlas(texture, spec, needed, font_path):
    from PIL import Image, ImageDraw, ImageFont

    _, _, width, height, size, _, _ = spec
    raw = bytearray(texture.get_raw_data())
    pos = 4 + ((uint(raw, 0) + 3) & ~3)
    old_bytes = width * height * 2
    start = len(raw) - old_bytes
    if (
        texture.type.name != "Texture2D"
        or start < pos + 16
        or struct.unpack_from("<4I", raw, pos) != (width, height, old_bytes, 13)
        or uint(raw, start - 4) != old_bytes
    ):
        raise ValueError("字库图集不是支持的单层 RGBA4444 格式")
    cell = max(12, round(size * 0.8))
    pixelsize = cell - 4
    cols = width // cell
    if not cols:
        raise ValueError("字形宽度超过图集宽度")
    new_height = 1 << (height + math.ceil(len(needed) / cols) * cell - 1).bit_length()
    if new_height > 4096:
        raise ValueError("译文字形超过单张图集可容纳的数量")
    font = ImageFont.truetype(str(font_path), pixelsize)
    try:
        if b"Bold" in font.get_variation_names():
            font.set_variation_by_name("Bold")
    except OSError:
        pass
    alpha = Image.new("L", (width, new_height - height))
    draw = ImageDraw.Draw(alpha)
    records = bytearray()
    for i, code in enumerate(needed):
        x, y = i % cols * cell + 1, i // cols * cell + 1
        draw.text((x, y + round(pixelsize * 0.86)), chr(code), font=font, anchor="ls", fill=255)
        records.extend(
            struct.pack(
                "<10i", code, x, y + height, cell - 2, cell - 2, 0, round(size / 15), cell - 1, 15, 0
            )
        )
    values = alpha.transpose(Image.Transpose.FLIP_TOP_BOTTOM).tobytes()
    pixels = bytearray(b"\xff\xff" * len(values))
    pixels[::2] = values.translate(bytes(240 | v >> 4 for v in range(256)))
    struct.pack_into("<II", raw, pos + 4, new_height, width * new_height * 2)
    struct.pack_into("<I", raw, start - 4, width * new_height * 2)
    texture.set_raw_data(raw[:start] + pixels + raw[start:])
    return new_height, records


def change_asset(asset, mapping, chars, font_path):
    tables = fonts = edited = 0
    linked = defaultdict(list)
    for obj in asset.objects.values():
        if obj.type.name == "TextAsset":
            entry = table(obj)
            if entry:
                pos, length, text = entry

                def replace(match):
                    value = match[2].strip()
                    return match[1] + " = " + mapping[value] if value in mapping else match[0]

                new = LINE.sub(replace, text)
                if new != text:
                    raw = obj.get_raw_data()
                    data = new.encode("utf-8-sig" if raw[pos + 4 : pos + 7] == b"\xef\xbb\xbf" else "utf8")
                    obj.set_raw_data(
                        raw[:pos]
                        + struct.pack("<I", len(data))
                        + data
                        + bytes(-len(data) % 4)
                        + raw[pos + 4 + ((length + 3) & ~3) :]
                    )
                    tables += 1
        elif obj.type.name == "MonoBehaviour":
            spec = bitmap_font(asset, obj)
            if spec:
                linked[spec[-1]].append((obj, spec))
    for texture_id, group in linked.items():
        spec = group[0][1]
        if any(s[2:6] != spec[2:6] for _, s in group):
            raise ValueError("共享图集的字形定义不一致")
        needed = sorted(chars - spec[5])
        fonts += len(group)
        if not needed:
            continue
        new_height, records = extend_atlas(asset.objects[texture_id], spec, needed, font_path)
        for obj, s in group:
            raw = bytearray(obj.get_raw_data())
            struct.pack_into("<I", raw, 60, new_height)
            struct.pack_into("<I", raw, s[0], len(s[5]) + len(needed))
            obj.set_raw_data(raw[: s[1]] + records + raw[s[1] :])
            edited += 1
    return tables, fonts, edited


def transform(decoded, use_model, progress):
    root = decoded / "assets"
    obbs = list(root.rglob("*.obb"))
    if len(obbs) > 1:
        raise ValueError("暂不支持多个 Unity 扩展包")
    outer = {p.relative_to(decoded).as_posix(): p for p in root.rglob("*") if p.is_file()}
    english = []
    seen_files = 0
    font_count = 0
    table_count = 0

    def visit(names, read, length, apply=False):
        nonlocal seen_files, font_count, table_count
        changes = {}
        for base, parts in groups(names):
            total = sum(length(n) for n in parts)
            if total > MAX_FILE:
                raise ValueError("Unity 单个资源超过扫描上限")
            raw = b"".join(read(n) for n in parts)
            asset = load_asset(raw)
            if not asset:
                continue
            if not apply:
                seen_files += 1
                for o in asset.objects.values():
                    if o.type.name == "TextAsset" and (entry := table(o)):
                        english.extend(v.strip() for _, v in LINE.findall(entry[2]))
                continue
            nt, nf, edited = change_asset(asset, mapped, chars, font)
            font_count += nf
            table_count += nt
            if not nt and not edited:
                continue
            data = asset.save()
            reopened = load_asset(data)
            if (
                reopened is None
                or asset.objects.keys() != reopened.objects.keys()
                or any(
                    (o.data if o.data is not None else o.get_raw_data())
                    != reopened.objects[k].get_raw_data()
                    for k, o in asset.objects.items()
                )
            ):
                raise ValueError("Unity 资源回读与预期不符")
            if parts == [base]:
                changes[base] = data
            else:
                chunk = length(parts[0])
                if chunk <= 0 or any(length(p) != chunk for p in parts[:-1]):
                    raise ValueError("Unity 分片尺寸不一致")
                changes.update(dict.fromkeys(parts))
                changes.update(
                    {
                        base + ".split" + str(i // chunk): data[i : i + chunk]
                        for i in range(0, len(data), chunk)
                    }
                )
        return changes

    def scan_obb(z, apply=False):
        infos = z.infolist()
        if (
            len(infos) > 20000
            or sum(i.file_size for i in infos) > 1024**3
            or len(set(z.namelist())) != len(infos)
        ):
            raise ValueError("扩展包目录重复或超过解压上限")
        return visit(z.namelist(), z.read, lambda n: z.getinfo(n).file_size, apply)

    def scan_outer(apply=False):
        return visit(outer, lambda n: outer[n].read_bytes(), lambda n: outer[n].stat().st_size, apply)

    scan_outer()
    if obbs:
        with ZipFile(obbs[0]) as z:
            scan_obb(z)
    if not english:
        raise ValueError("未找到支持的 Unity 4 英文键值语言表，无法生成游戏汉化包")
    progress(f"已分析 {seen_files} 个 Unity 资源文件，发现 {len(set(english))} 个不同文本")
    mapped, model_count = translations(english, use_model, progress)
    chars = {ord(c) for v in mapped.values() for c in v if ord(c) > 127}
    fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    font = next(
        (fonts / n for n in ["NotoSansSC-VF.ttf", "msyhbd.ttc", "simhei.ttf"] if (fonts / n).is_file()),
        None,
    )
    if not font:
        raise ValueError("未找到简体中文字体，请安装 Noto Sans SC 或微软雅黑")
    progress("正在沿字库材质引用定位图集，补字并回读修改后的资源")
    for name, data in scan_outer(True).items():
        target = decoded / name
        if data is None:
            target.unlink()
        else:
            target.write_bytes(data)
    if obbs:
        obb = obbs[0]
        with tempfile.TemporaryDirectory(dir=obb.parent) as temp:
            new = Path(temp) / "resources.obb"
            with ZipFile(obb) as src:
                changes = scan_obb(src, True)
                if changes:
                    with ZipFile(new, "w") as dst:
                        dst.comment = src.comment
                        for info in src.infolist():
                            if info.filename in changes:
                                data = changes.pop(info.filename)
                                if data is not None:
                                    dst.writestr(info, data)
                            else:
                                with src.open(info) as inp, dst.open(info, "w") as out:
                                    shutil.copyfileobj(inp, out)
                        for name, data in changes.items():
                            dst.writestr(name, data)
            if new.exists():

                def expansion_key(p):
                    with p.open("rb") as f:
                        f.seek(max(0, p.stat().st_size - 65558))
                        return hashlib.md5(f.read()).hexdigest().encode()

                settings = list(root.rglob("settings.xml"))
                token = b'<bool name="' + expansion_key(obb) + b'">True</bool>'
                matches = [p for p in settings if token in p.read_bytes()]
                if len(matches) != 1:
                    raise ValueError("无法唯一定位 Unity 扩展包校验标识")
                settings = matches[0]
                settings.write_bytes(
                    settings.read_bytes().replace(
                        token, token.replace(expansion_key(obb), expansion_key(new))
                    )
                )
                new.replace(obb)
    if not table_count or not font_count:
        raise ValueError("未找到可核对的 NGUI 字库与语言表组合")
    return {
        "kind": "unity-ngui-zh",
        "translated": len(mapped),
        "discovered": len(set(english)),
        "tables": table_count,
        "fonts": font_count,
        "model_translations": model_count,
        "resource_files": seen_files,
    }
