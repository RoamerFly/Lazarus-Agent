"""Build the Pacific Rim Chinese trial from the workbench-verified ARM32 repair.

Developer recipe: install UnityPy==1.25.4 and Pillow, and supply a CJK font.
Uses the workbench APK editor and signing key. Never overwrites the input.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import struct
import sys
import tempfile
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from apk_edit_build import build_modified
from bench_device import TOOLS

BASE_SHA = "055b9441a40c46489e6da66e460003dfc356e1a247465490bf08d0224de1dddb"
PREFIX = "assets/bin/Data/"
OBB = "assets/sdcard/Android/obb/com.jumpgames.pacificrim/main.16.com.jumpgames.pacificrim.obb"
CHUNK = 1048576
ZH = {
    "Button_TapToContinue": "轻触继续", "MainMenu_But_StoryMode": "生涯模式",
    "MainMenu_But_SurvivalMode": "生存模式", "MainMenu_But_GameCenter": "游戏中心",
    "MainMenu_But_Stats": "战绩", "MainMenu_But_Store": "黑市",
    "MainMenu_But_Help&Options": "帮助与设置", "MainMenu_Settings_Settings": "设置",
    "MainMenu_Settings_AudioVolume": "音量", "MainMenu_Settings_Language": "语言",
    "MainMenu_Settings_English": "简体中文", "MainMenu_Settings_Credits": "制作人员",
    "MainMeNu_Loading_VR": "正在启动模拟训练…",
    "MainMenu_Loading_Dialogue": r"欢迎加入环太平洋防卫军。我是指挥官潘特科斯特。\n先到模拟器里练习基本操作。\n准备好后，点按下方的出击。",
    "Button_Back": "返回", "Button_Next": "下一步", "Button_Retry": "重试",
    "Button_Deploy": "出击", "Button_Redeploy": "再次出击", "Button_Resume": "继续",
    "Button_Exit": "退出", "Button_Continue": "继续", "Loading": "正在出击…",
    "Button_Exit_Battle": "退出战斗", "Button_Restart": "重新开始",
    "Tutorial_Block": "按住格挡", "Tutorial_Block_Subtext": "格挡可以减少伤害。",
    "Tutorial_Evade_Left": "点按闪避", "Tutorial_Evade_Right": "点按闪避",
    "Tutorial_Evade_Left_Subtext": "躲开敌人的攻击。", "Tutorial_Evade_Right_Subtext": "掌握得不错，再试一次。",
    "Tutorial_Attack1": "滑动攻击", "Tutorial_Attack1_Subtext": "做得好。",
    "Tutorial_Crouch": "按住下蹲", "Tutorial_Crouch_Subtext": "下蹲可以躲过高位攻击。",
    "Tutorial_Parry1": "滑动招架", "Tutorial_Parry2": "滑动招架", "Tutorial_Parry3": "滑动招架",
    "Tutorial_SpecialStart": "轻触继续", "Tutorial_WeaponStart": "轻触继续", "Tutorial_WeaponEnd": "轻触继续",
}


def u32(data, pos):
    return struct.unpack_from("<I", data, pos)[0]


def edit_language(obj):
    raw = obj.get_raw_data()
    if raw[4:11] != b"English":
        raise ValueError("Unexpected language table")
    length = u32(raw, 12)
    original = raw[16:16 + length].decode("utf8")
    seen = set()

    def substitute(match):
        key = match[1]
        if key not in ZH:
            return match[0]
        if key in seen:
            raise ValueError(f"Duplicate language key {key}")
        seen.add(key)
        return key + " = " + ZH[key]

    translated = re.sub(r"(?m)^([^=\r\n]+?)\s*=[^\r\n]*", substitute, original).encode("utf8")
    if seen != ZH.keys():
        raise ValueError(f"Missing language keys: {ZH.keys() - seen}")
    obj.set_raw_data(raw[:12] + struct.pack("<I", len(translated)) + translated
                     + bytes(-len(translated) % 4) + raw[16 + ((length + 3) & ~3):])


def glyphs(font_path):
    from PIL import Image, ImageDraw, ImageFont
    chars = sorted({c for value in ZH.values() for c in value if ord(c) > 127 and c != "…"})
    font = ImageFont.truetype(str(font_path), 44)
    try:
        if b"Bold" in font.get_variation_names():
            font.set_variation_by_name("Bold")
    except OSError:
        pass  # Static fonts already use the face selected by the caller.
    cell, columns = 48, 10
    height = 1 << math.ceil(math.log2(512 + math.ceil(len(chars) / columns) * cell))
    extra = Image.new("L", (512, height - 512))
    draw = ImageDraw.Draw(extra)
    records = bytearray()
    for index, char in enumerate(chars):
        x, y = index % columns * cell + 1, index // columns * cell + 1
        draw.text((x, y + 38), char, anchor="ls", font=font, fill=255)
        records.extend(struct.pack("<10i", ord(char), x, y + 512, cell-2, cell-2, 0, 4, cell-1, 15, 0))
    # The little-endian RGBA4444 atlas stores bottom-up rows and alpha in the low nibble.
    alpha = extra.transpose(Image.Transpose.FLIP_TOP_BOTTOM).tobytes()
    pixels = bytearray(b"\xff\xff" * len(alpha))
    pixels[::2] = alpha.translate(bytes(0xf0 | (value >> 4) for value in range(256)))
    return len(chars), height, records, pixels


def edit_font(obj, count, height, records):
    raw = bytearray(obj.get_raw_data())
    if struct.unpack_from("<2I", raw, 56) != (512, 512):
        raise ValueError("Unexpected NGUI font dimensions")
    struct.pack_into("<I", raw, 60, height)
    pos = 68 + ((u32(raw, 64) + 3) & ~3)
    old_count = u32(raw, pos)
    struct.pack_into("<I", raw, pos, old_count + count)
    pos += 4
    for _ in range(old_count):
        pos += 40 + 4 * u32(raw, pos + 36)
    if len(raw) - pos != 52:
        raise ValueError("Unexpected NGUI glyph layout")
    obj.set_raw_data(raw[:pos] + records + raw[pos:])


def edit_texture(obj, height, pixels):
    raw = bytearray(obj.get_raw_data())
    pos = 4 + ((u32(raw, 0) + 3) & ~3)
    if struct.unpack_from("<4I", raw, pos) != (512, 512, 512*512*2, 13):
        raise ValueError("Unexpected texture format")
    start = len(raw) - 512 * 512 * 2
    if u32(raw, start-4) != 512*512*2:
        raise ValueError("Unexpected pixel layout")
    struct.pack_into("<II", raw, pos+4, height, 512*height*2)
    struct.pack_into("<I", raw, start-4, 512*height*2)
    obj.set_raw_data(raw[:start] + pixels + raw[start:])


def asset_parts(number, read, exists, font_data):
    import UnityPy
    name = PREFIX + f"sharedassets{number}.assets.split"
    old = []
    while exists(name + str(len(old))):
        old.append(name + str(len(old)))
    original = b"".join(map(read, old))
    asset = next(iter(UnityPy.load(original).files.values()))
    if number == 0:
        edit_language(asset.objects[9])
    else:
        count, height, records, pixels = font_data
        fonts, textures = {1: ([24, 25], [6, 9]), 3: ([123, 124, 125], [25, 17, 27])}[number]
        for idx in fonts:
            edit_font(asset.objects[idx], count, height, records)
        for idx in textures:
            edit_texture(asset.objects[idx], height, pixels)
    data = asset.save()
    reopened = next(iter(UnityPy.load(data).files.values()))
    if set(asset.objects) != set(reopened.objects) or any(
        (obj.data if obj.data is not None else obj.get_raw_data()) != reopened.objects[k].get_raw_data() for k, obj in asset.objects.items()
    ):
        raise ValueError("Serialized Unity object roundtrip mismatch")
    parts = {name + str(i // CHUNK): data[i:i + CHUNK] for i in range(0, len(data), CHUNK)}
    return set(old), parts


def expansion_id(path):
    # Unity 4 identifies an expansion file by the MD5 of its final 65,558 bytes.
    with path.open("rb") as stream:
        stream.seek(-65558, 2)
        return hashlib.md5(stream.read()).hexdigest().encode("ascii")


def transform(decoded, font_path):
    font_data = glyphs(font_path)
    old, parts = asset_parts(0, lambda n: (decoded/n).read_bytes(), lambda n: (decoded/n).is_file(), font_data)
    for n in old - parts.keys():
        (decoded/n).unlink()
    for n, data in parts.items():
        (decoded/n).write_bytes(data)
    obb = decoded / OBB
    settings = decoded / PREFIX / "settings.xml"
    xml = settings.read_bytes()
    old_key = b'<bool name="' + expansion_id(obb) + b'">True</bool>'
    if xml.count(old_key) != 1:
        raise ValueError("Unity settings do not identify the original expansion")
    with tempfile.TemporaryDirectory(dir=obb.parent) as tmp:
        new = Path(tmp)/"patched.obb"
        with ZipFile(obb) as src, ZipFile(new, "w") as dst:
            entries = set(src.namelist())
            old, parts = set(), {}
            for number in (1, 3):
                replaced, additions = asset_parts(number, src.read, entries.__contains__, font_data)
                old.update(replaced)
                parts.update(additions)
            for info in src.infolist():
                if info.filename not in old:
                    with src.open(info) as inp, dst.open(info, "w") as out:
                        shutil.copyfileobj(inp, out)
                elif info.filename in parts:
                    dst.writestr(info, parts.pop(info.filename))
            for name, data in parts.items():
                dst.writestr(name, data)
            dst.comment = src.comment
        new_key = b'<bool name="' + expansion_id(new) + b'">True</bool>'
        settings.write_bytes(xml.replace(old_key, new_key))
        new.replace(obb)
    return {"kind":"pacific-rim-zh-trial", "translated_keys":len(ZH), "added_glyphs_per_font":font_data[0]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "output", "font"):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    with args.source.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != BASE_SHA:
            parser.error("This recipe requires the verified Pacific Rim ARM32 workbench APK.")
    result = build_modified(args.source.resolve(), args.output.resolve(), 36, TOOLS,
                            lambda decoded: transform(decoded, args.font))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
