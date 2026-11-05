"""Edit real browser captures from the current live Agent session into an MP4."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app_version import APP_VERSION

FONT = "C:/Windows/Fonts/msyh.ttc"


def wrapped(text, draw, font, width):
    lines, current = [], ""
    for char in text:
        if draw.textlength(current + char, font=font) > width:
            lines.append(current)
            current = char
        else:
            current += char
    return lines + [current]


def build(capture):
    metadata = json.loads((capture / "capture.json").read_text(encoding="utf-8"))
    if metadata["app_version"] != APP_VERSION:
        raise ValueError("演示捕获与当前程序版本不同")
    frames = list(metadata["frames"])
    if len(frames) < 6:
        raise ValueError("缺少实际操作画面")
    out = ROOT / "交付/遗留软件复活智能体演示.mp4"
    out.parent.mkdir(exist_ok=True)
    keyframes = [(0, "Windows 工作台。导入老 APK，选择 Android 16。", 5),
                 (1, "自有巡检单原包 targetSdk 22，Android 16 默认安装被拒绝。", 6),
                 (2, "在发送需求之前，固定路线切换和冷启动的验收步骤。", 7),
                 (3, "用一句需求要求恢复安装，并记住最后打开的路线。", 7)]
    middle = list(range(4, len(frames)-1))
    selected = middle[::max(1, len(middle)//8)][:8]
    for index in selected:
        keyframes.append((index, "DeepSeek Flash 实际执行节选。模型自主读取、修改、构建和测试。", 3))
    keyframes.append((len(frames)-1, "Android 16 专用模拟器独立复测，核对安装包哈希并执行固定的 10 步操作。", 9))
    device_screen = metadata.get("device_screen")
    if device_screen:
        if (device_screen["file"] != "device-final.png" or device_screen["api"] != 36
                or device_screen["apk_sha256"] != metadata["candidate_sha256"]):
            raise ValueError("Android 画面与候选身份不符")
        with (capture / device_screen["file"]).open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != device_screen["sha256"]:
                raise ValueError("Android 画面哈希不符")
        keyframes.append((len(frames), "验收后在专用模拟器上看到的应用界面，设备安装哈希与导出 APK 一致。", 7))
        frames.append(device_screen)
    with tempfile.TemporaryDirectory(dir=ROOT / "build", prefix="demo-") as directory:
        temporary = Path(directory)
        timeline = ["ffconcat version 1.0"]
        for number, (index, subtitle, duration) in enumerate(keyframes):
            source = capture / frames[index]["file"]
            image = Image.new("RGB", (1440, 1080), "#edf3f0")
            draw = ImageDraw.Draw(image)
            draw.rectangle((0, 0, 1440, 75), fill="#123e41")
            draw.text((32, 20), "遗留软件复活智能体", font=ImageFont.truetype(FONT, 27), fill="white")
            draw.text((840, 26), f"v{APP_VERSION} · 实测节选 · 等待过程加速", font=ImageFont.truetype(FONT, 19), fill="#cbebe3")
            with Image.open(source) as shot:
                scaled = ImageOps.contain(shot.convert("RGB"), (1410, 875), Image.Resampling.LANCZOS)
            image.paste(scaled, ((1440-scaled.width)//2, 80+(875-scaled.height)//2))
            draw.rectangle((0, 972, 1440, 1080), fill="#103d3d")
            font = ImageFont.truetype(FONT, 25)
            for line_number, line in enumerate(wrapped(subtitle, draw, font, 1360)):
                draw.text((32, 990+line_number*37), line, font=font, fill="white")
            name = f"frame-{number:03}.png"
            image.save(temporary / name)
            timeline.extend([f"file '{name}'", f"duration {duration}"])
        timeline.append(timeline[-2])
        (temporary / "timeline.txt").write_text("\n".join(timeline), encoding="ascii")
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                        "-i", "timeline.txt", "-vf", "fps=20,format=yuv420p", "-c:v", "libx264",
                        "-preset", "veryfast", "-crf", "19", "-movflags", "+faststart", str(out)],
                       cwd=temporary, check=True)
    with out.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    provenance = {"app_version": APP_VERSION, "video_sha256": checksum, "capture": metadata,
                  "edit": {"method": "real-screenshot-excerpts", "keyframes": keyframes,
                           "voiceover": False}, "duration_planned_seconds": sum(row[2] for row in keyframes)}
    out.with_suffix(".json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture_directory", type=Path)
    build(parser.parse_args().capture_directory)
