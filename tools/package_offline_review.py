"""Attach the same-build proposal and workbench demo to an offline review ZIP."""
from argparse import ArgumentParser
import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.package_agent_review import digest, read
from tools.verify_review_package import COLD_WINDOWS_FILE, LOCAL_DEMO, verify


def build(delivery, materials, capture, output):
    if output.exists():
        raise ValueError("请选择新的同版评审包输出路径")
    version = read(delivery / "本地构建回执.json")["version"]
    samples = delivery / f"v{version}-离线样本与Windows复核包.zip"
    original = verify(samples)
    status = original["package_status"]
    if (not (original["offline_windows"] or original["offline_cold_runtime"])
            or original["offline_materials"] or status["version"] != version
            or status["exe_sha256"] != digest(delivery / "Lazarus-Agent.exe")):
        raise ValueError("样本包与交付 EXE 不一致或已包含同版材料")
    with ZipFile(samples) as archive:
        files = {name: archive.read(name) for name in archive.namelist() if name != "SHA256SUMS.txt"}
    files["离线样本复测说明.md"] = files.pop("从这里开始.md")
    proposal = materials / "遗留软件复活智能体方案说明.pdf"
    video = materials / "遗留软件复活智能体离线演示.mp4"
    record = video.with_suffix(".json")
    source = delivery / f"F01-源代码-v{version}.zip"
    if not proposal.read_bytes().startswith(b"%PDF-"):
        raise ValueError("同版方案不是 PDF")
    files.update({proposal.name: proposal.read_bytes(), video.name: video.read_bytes(), source.name: source.read_bytes(),
                  "演示记录.json": record.read_bytes(), "演示设备画面.png": (capture / "device-final.png").read_bytes(),
                  LOCAL_DEMO + "base.apk": files["可分发样本/P01/base.apk"],
                  LOCAL_DEMO + "candidate.apk": (capture / "candidate.apk").read_bytes(),
                  LOCAL_DEMO + "agent-acceptance.json": (capture / "agent-acceptance.json").read_bytes()})
    receipt = {"app_version": version, "source_commit": status["source_commit"], "exe_sha256": status["exe_sha256"],
               "source_archive_sha256": digest(source), "proposal_sha256": digest(proposal),
               "video_sha256": digest(video), "video_record_sha256": digest(record),
               "video_seconds": read(record)["duration_planned_seconds"],
               "device_screen_sha256": digest(capture / "device-final.png"),
               "acceptance_sha256": digest(capture / "agent-acceptance.json")}
    status["offline_materials"] = "same-build-offline-review"
    files["复核状态.json"] = json.dumps(status, ensure_ascii=False, indent=2).encode()
    files["同版材料校验回执.json"] = json.dumps(receipt, ensure_ascii=False, indent=2).encode()
    if original["offline_cold_runtime"]:
        windows_note = (f"[同版空目录准备回执]({COLD_WINDOWS_FILE})\n\n"
                        "专用 Windows 虚拟机从空目录完成组件下载与复用，另完成隔离安装、自检及卸载。"
                        "交互桌面启动和虚拟机内的 Android 行为仍待复核。")
    else:
        windows_note = ("[干净 Windows 回执](Windows复核/干净Windows复测回执.json)\n\n"
                        "干净 Windows 已验证安装、桌面启动和两份样本输入，首次下载 Android 环境和设备步骤仍待同版复核。")
    files["从这里开始.md"] = fr"""# v{version} F01 同版离线评审

[六页方案](遗留软件复活智能体方案说明.pdf) · [工作台演示](遗留软件复活智能体离线演示.mp4) · [构建源码](F01-源代码-v{version}.zip) · [同版回执](同版材料校验回执.json)

演示录制使用同版源码工作台。导入巡检 APK 并从对话提交需求后，重放留档工具操作，实际回编、签名并通过 Android 16 的十步独立验收。没有调用真实模型。原包、候选、报告和模拟器画面均可核对。

解压后核对文件与演示输入。便携 EXE 单列，命令中使用其实际路径。

    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\校验文件.ps1
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\复测样本.ps1 -LocalBuild -App "..\Lazarus-Agent.exe" -Sample 工作台演示 -CheckOnly

准备好 Android 16 后去掉 -CheckOnly 可复跑十步。[其他样本及反例说明](离线样本复测说明.md) · {windows_note}

v0.6.24 的全组开发回归作为历史证据单列。
""".encode()
    files["SHA256SUMS.txt"] = "".join(
        f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in sorted(files.items())
    ).encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent, prefix="offline-review-") as temporary:
        staged = Path(temporary) / "review.zip"
        with ZipFile(staged, "x", ZIP_DEFLATED) as archive:
            for name, data in sorted(files.items()):
                archive.writestr(ZipInfo(name), data, compress_type=ZIP_DEFLATED)
        checked = verify(staged)
        if (not checked["offline_materials"] or len(checked["samples"]) != len(original["samples"]) + 1
                or staged.stat().st_size > 100 * 1024 * 1024):
            raise ValueError("同版评审包的范围、样本或大小不符")
        staged.rename(output)
    return {**receipt, "archive_sha256": digest(output), "bytes": output.stat().st_size,
            "file_count": checked["file_count"], "offline_materials": checked["offline_materials"]}


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    for name in ("delivery", "materials", "capture", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    paths = {name: path.resolve() for name, path in vars(parser.parse_args()).items()}
    print(json.dumps(build(**paths), ensure_ascii=False, indent=2))
