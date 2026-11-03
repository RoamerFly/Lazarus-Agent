"""Edit actual captured workbench frames and independent verification screens into an MP4."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile
from PIL import Image,ImageDraw,ImageFont,ImageOps

ROOT=Path(__file__).resolve().parents[1]
WEB=ROOT/"evidence/peer-web"
FONT="C:/Windows/Fonts/msyh.ttc"


def build(capture):
    clips=json.loads((capture/"clips.json").read_text(encoding="utf-8"))
    live=sorted(capture.glob("live-*.png"))
    if not live:raise ValueError("Missing interactive recording")
    out=ROOT/"交付/遗留软件复活智能体演示.mp4"
    out.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as d:
        tmp=Path(d)
        lines=["ffconcat version 1.0"]
        def frame(source,phase,subtitle,duration):
            img=Image.new("RGB",(1440,1080),"#edf3f0");draw=ImageDraw.Draw(img)
            draw.rectangle((0,0,1440,70),fill="#123e41")
            draw.text((40,17),"遗留软件复活智能体",font=ImageFont.truetype(FONT,25),fill="white")
            draw.text((1000,21),phase,font=ImageFont.truetype(FONT,19),fill="#cbebe3")
            with Image.open(source) as source_img:
                scaled=ImageOps.contain(source_img.convert("RGB"),(1410,900),method=Image.Resampling.LANCZOS)
            img.paste(scaled,((1440-scaled.width)//2,76+(900-scaled.height)//2))
            draw.rectangle((0,986,1440,1080),fill="#103d3d")
            draw.text((40,1010),subtitle,font=ImageFont.truetype(FONT,27),fill="white")
            f=f"frame-{len(lines):03}.png"; img.save(tmp/f)
            lines.extend([f"file '{f}'",f"duration {duration}"])
        for clip in clips:
            frame(capture/clip["fn"],"实测节选  ·  Android 15",clip["title"],clip["seconds"])
        for f in live:
            frame(f,"交互录屏  ·  专用模拟器","在工作台中直接操作已安装的修复应用",.25)
        frame(WEB/"archive-broken-api35-final-page.png","独立复测  ·  工程样本","第二类故障发生在点击之后。修复后，相同操作成功读取归档内容。",6)
        frame(WEB/"archive-broken-api35-after-tap.png","独立复测  ·  修复包","在导出的修复包中再次点击，界面显示 Read OK: HTTP/1.1。",4)
        frame(WEB/"google-my-tracks-api35-limit-page.png","限制分支  ·  实测记录","原生库与目标设备架构不匹配时，工作台报告限制并停止导出。",6)
        frame(WEB/"oi-api35-final-page.png","重邮GGBond  ·  F01","交付内容包括独立 APK 和诊断报告。本次实测使用本地规则。",5)
        lines.append(lines[-2])
        (tmp/"timeline.txt").write_text("\n".join(lines),encoding="ascii")
        subprocess.run(["ffmpeg","-hide_banner","-loglevel","error","-y","-f","concat","-safe","0",
                        "-i","timeline.txt","-vf","fps=20,format=yuv420p","-c:v","libx264",
                        "-preset","veryfast","-crf","18","-movflags","+faststart",str(out)],cwd=tmp,check=True)
    print(out)


if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("capture_directory",type=Path)
    build(p.parse_args().capture_directory)
