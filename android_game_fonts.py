"""Inspect Android outline fonts and extend XML-described GHL bitmap font pages."""

from pathlib import Path
import math
import os
import xml.etree.ElementTree as ET
from PIL import Image, ImageDraw, ImageFont


def update(assets, values):
    chars = {c for value in values for c in value if ord(c) > 127}
    outline = []
    for p in assets.rglob("*"):
        if p.suffix.lower() not in {".ttf", ".otf"} or not p.is_file():
            continue
        from fontTools.ttLib import TTFont

        with TTFont(p, fontNumber=0) as font:
            cmap = font.getBestCmap() or {}
            outline.append({"name": p.name, "missing": sum(ord(c) not in cmap for c in chars)})
    changed = added = 0
    for p in assets.rglob("*.xml"):
        if p.stat().st_size > 2 * 1024**2:
            continue
        try:
            root = ET.parse(p).getroot()
        except ET.ParseError:
            continue
        if root.tag != "font" or root.get("type") != "GHL":
            continue
        glyphs = root.find("chars")
        texture = root.find("texture")
        metrics = root.find("metrics")
        desc = root.find("description")
        if any(node is None for node in (glyphs, texture, metrics, desc)):
            raise ValueError("GHL 字库缺少字符、图集或排版信息")
        name = texture.get("file", "")
        if not name or Path(name).name != name or ":" in name or not name.lower().endswith(".png"):
            raise ValueError("GHL 字库引用的图集路径不受支持")
        missing = sorted(chars - {g.get("id") for g in glyphs.findall("char")})
        if not missing:
            continue
        size = int(desc.get("size"))
        width = int(texture.get("width"))
        height = int(texture.get("height"))
        if not (6 <= size <= 128 and 32 <= width <= 4096 and 16 <= height <= 4096):
            raise ValueError("GHL 字库尺寸超出支持范围")
        dir_fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        font_path = next(
            (
                dir_fonts / n
                for n in ("NotoSansSC-VF.ttf", "msyh.ttc", "simhei.ttf")
                if (dir_fonts / n).is_file()
            ),
            None,
        )
        if font_path is None:
            raise ValueError("补字需要本机安装 Noto Sans SC 或微软雅黑")
        from fontTools.ttLib import TTFont

        with TTFont(font_path, fontNumber=0) as donor:
            available = donor.getBestCmap() or {}
            if any(ord(c) > 0xFFFF or ord(c) not in available for c in missing):
                raise ValueError("本机字体或 GHL 单字符格式无法表示全部译文字形")
        font = ImageFont.truetype(str(font_path), size)
        try:
            if b"Regular" in font.get_variation_names():
                font.set_variation_by_name("Regular")
        except OSError:
            pass
        boxes = [font.getbbox(c, anchor="ls") for c in missing]
        cell_w = max(b[2] - b[0] for b in boxes) + 2
        cell_h = max(b[3] - b[1] for b in boxes) + 2
        cols = width // cell_w
        if not cols:
            raise ValueError("GHL 图集宽度不足")
        new_h = 1 << (height + math.ceil(len(missing) / cols) * cell_h - 1).bit_length()
        if new_h > 4096:
            raise ValueError("需要补充的字形超过 GHL 单图集上限")
        tex = p.with_name(name)
        with Image.open(tex) as original:
            if original.size != (width, height):
                raise ValueError("GHL 图集与声明尺寸不同")
            image = Image.new("RGBA", (width, new_h), (255, 255, 255, 0))
            image.paste(original.convert("RGBA"), (0, 0))
        draw = ImageDraw.Draw(image)
        for i, (ch, (left, top, right, bottom)) in enumerate(zip(missing, boxes)):
            x = i % cols * cell_w + 1
            y = height + i // cols * cell_h + 1
            draw.text((x - left, y - top), ch, font=font, anchor="ls", fill="white")
            ET.SubElement(
                glyphs,
                "char",
                {
                    "id": ch,
                    "offset": f"{left} {-top}",
                    "rect": f"{x} {y} {right - left} {bottom - top}",
                    "advance": str(round(font.getlength(ch))),
                },
            )
        image.save(tex)
        texture.set("height", str(new_h))
        ET.ElementTree(root).write(p, encoding="utf-8", xml_declaration=True)
        added += len(missing)
        changed += 1
    return {"outline_fonts": outline, "bitmap_atlases_updated": changed, "bitmap_glyphs_added": added}
