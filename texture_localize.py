"""Recognize labels in loose UI rasters and repaint only their localized regions."""

from collections import Counter
from pathlib import Path
import re
import os
import hashlib

from texture_memory import REGIONS

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

LABEL = re.compile(r"[A-Za-z][A-Za-z0-9 &'’!?.:+%/\-]{1,39}")
UI_PATH = re.compile(
    r"ui|interface|menu|button|btn|label|title|atlas|plist|panel|dialog|splash|loading|intro|screen|reward|banner|locale",
    re.I,
)


def candidates(decoded):
    for base in (decoded / "assets", decoded / "res"):
        for path in sorted(base.rglob("*")):
            if path.suffix.lower() not in {
                ".png",
                ".jpg",
                ".jpeg",
                ".webp",
            } or path.name.endswith(".9.png"):
                continue
            rel = path.relative_to(base)
            if (
                not UI_PATH.search(rel.as_posix())
                and not rel.parts[0].startswith("drawable")
            ) or "font" in rel.as_posix().lower():
                continue
            if any(
                re.fullmatch(r"(?:ko|ja|zh|fr|de|es|ru)(?:[-_].*)?", part, re.I)
                for part in rel.parts[:-1]
            ):
                continue
            yield path


def scan(decoded, progress):
    paths = list(candidates(decoded))
    if not paths:
        return {}
    from rapidocr_onnxruntime import RapidOCR

    ocr = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)
    result = {}
    for i, path in enumerate(paths):
        if i % 40 == 0:
            progress(f"正在识别界面图片文字（{i + 1}/{len(paths)}）")
        if path.stat().st_size > 48 * 1024**2:
            continue
        reviewed = REGIONS.get(hashlib.sha256(path.read_bytes()).hexdigest())
        if reviewed:
            result[path] = [(text, 1.0, box) for text, box in reviewed]
            continue
        with Image.open(path) as im:
            if min(im.size) < 12 or im.width * im.height > 4096**2:
                continue
            rgba = im.convert("RGBA")
        bg = Image.new("RGB", rgba.size, "white")
        bg.paste(rgba, mask=rgba.getchannel("A"))
        scale = min(2, 1280 / max(bg.size))
        view = bg.resize(
            (max(1, round(bg.width * scale)), max(1, round(bg.height * scale))),
            Image.Resampling.LANCZOS,
        )
        rows, _ = ocr(cv2.cvtColor(np.array(view), cv2.COLOR_RGB2BGR))
        labels = []
        for polygon, value, score in rows or []:
            value = value.strip()
            if not LABEL.fullmatch(value) or float(score) < 0.60:
                continue
            poly = np.array(polygon) / scale
            left, top = np.floor(poly.min(axis=0)).astype(int)
            right, bottom = np.ceil(poly.max(axis=0)).astype(int)
            box = (
                max(0, int(left)),
                max(0, int(top)),
                min(im.width, int(right)),
                min(im.height, int(bottom)),
            )
            if box[2] - box[0] >= 8 and box[3] - box[1] >= 6:
                labels.append((value, float(score), box))
        if labels:
            result[path] = labels
    return result


def accepted(result):
    from translation_memory import MEMORY

    counts = Counter(text.casefold() for rows in result.values() for text, _, _ in rows)
    return {
        p: [
            (text, box)
            for text, score, box in labels
            if (
                score >= 0.91
                or (text.casefold() in MEMORY and score >= 0.64)
                or (score >= 0.80 and counts[text.casefold()] >= 2)
            )
        ]
        for p, labels in result.items()
    }


def repaint(path, regions):
    with Image.open(path) as original:
        image = original.convert("RGBA")
        fmt = original.format
    font_dir = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    font_path = next(
        (
            font_dir / n
            for n in ("msyhbd.ttc", "simhei.ttf", "NotoSansSC-VF.ttf")
            if (font_dir / n).exists()
        ),
        None,
    )
    if not font_path:
        raise ValueError("界面贴图重绘需要安装简体中文字体")
    for box, text in regions:
        l, t, r, b = box
        pad = max(2, (b - t) // 8)
        crop = (
            max(0, l - pad * 2),
            max(0, t - pad * 2),
            min(image.width, r + pad * 2),
            min(image.height, b + pad * 2),
        )
        x, y = l - crop[0], t - crop[1]
        pixels = np.array(image.crop(crop))
        mask = np.zeros(pixels.shape[:2], np.uint8)
        cv2.rectangle(
            mask,
            (max(0, x - 1), max(0, y - 1)),
            (min(mask.shape[1] - 1, x + r - l), min(mask.shape[0] - 1, y + b - t)),
            255,
            -1,
        )
        border = pixels[mask == 0]
        rgb = cv2.inpaint(pixels[:, :, :3].copy(), mask, max(2, pad), cv2.INPAINT_TELEA)
        alpha = (
            cv2.inpaint(pixels[:, :, 3], mask, max(2, pad), cv2.INPAINT_TELEA)
            if len(border)
            else np.zeros_like(mask)
        )
        tile = Image.fromarray(np.dstack((rgb, alpha)))
        size = max(6, b - t)
        while True:
            font = ImageFont.truetype(str(font_path), size)
            bounds = font.getbbox(text, stroke_width=1)
            if size <= 4 or (
                bounds[2] - bounds[0] <= r - l and bounds[3] - bounds[1] <= b - t
            ):
                break
            size -= 1
        draw = ImageDraw.Draw(tile)
        cx = x + (r - l - bounds[2] - bounds[0]) / 2
        cy = y + (b - t - bounds[3] - bounds[1]) / 2
        draw.text(
            (cx, cy),
            text,
            font=font,
            fill="#ffefd0",
            stroke_width=1,
            stroke_fill="#382411",
        )
        image.paste(tile, crop[:2])
    if fmt == "JPEG":
        image.convert("RGB").save(path, quality=96)
    else:
        image.save(path, format=fmt, **({"lossless": True} if fmt == "WEBP" else {}))
