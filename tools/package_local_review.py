"""Assemble an engineering review ZIP from a verified local regression and same-build materials."""
from argparse import ArgumentParser
import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.verify_review_package import LOCAL_DEMO, verify


def build(materials, screen, output):
    if output.exists():
        raise ValueError("请选择新的评审包输出路径")
    receipt = json.loads((materials / "同版材料校验回执.json").read_text(encoding="utf-8-sig"))
    version = receipt["app_version"]
    regression = materials / f"v{version}-本地开发回归复测包.zip"
    checked = verify(regression)
    status = checked["package_status"]
    if checked["local_regression"] is None or checked["local_review"] is not None:
        raise ValueError("请提供已校验的原始本地回归 ZIP")
    with (materials / "Lazarus-Agent.exe").open("rb") as executable:
        if hashlib.file_digest(executable, "sha256").hexdigest() != status["exe_sha256"]:
            raise ValueError("同目录的便携 EXE 与原始回归不一致")
    with ZipFile(regression) as archive:
        files = {name: archive.read(name) for name in archive.namelist() if name != "SHA256SUMS.txt"}
    for name in (
        "遗留软件复活智能体方案说明.pdf", "遗留软件复活智能体演示.mp4",
        "演示记录.json", "同版材料校验回执.json",
        f"F01-源代码-v{version}.zip",
        f"v{version}-Windows隔离安装复核工具.zip", "Windows安装复核工具回执.json",
        f"v{version}-归档阅读器专项复测输入-r3.zip", "归档复测环境回执.json",
    ):
        files[name] = (materials / name).read_bytes()
    files["演示设备画面.png"] = screen.read_bytes()
    for name in ("base.apk", "candidate.apk", "agent-acceptance.json"):
        files[LOCAL_DEMO + name] = (materials / "演示复测" / name).read_bytes()
    status["local_review"] = {
        "scope": "local-engineering-review", "clean_windows": "pending", "non_developer_trial": "pending",
    }
    files["复核状态.json"] = json.dumps(status, ensure_ascii=False, indent=2).encode()
    files["从这里开始.md"] = fr"""# v{version} F01 本地工程评审包

本包汇集同一实现的六页方案、工作台演示、源码、固定回归和独立复测入口。Windows 便携 EXE 随本地交付目录单列，解压后使用同版程序，脚本核对构建回执中的 SHA-256。

- [方案说明](遗留软件复活智能体方案说明.pdf) · [实际工作台演示](遗留软件复活智能体演示.mp4) · [录制回执](演示记录.json)
- [构建源码](F01-源代码-v{version}.zip) · [完整计分](results.json) · [差异审查](diff-review.json)
- [Windows 隔离安装复核工具](v{version}-Windows隔离安装复核工具.zip) · [归档阅读器复测工具](v{version}-归档阅读器专项复测输入-r3.zip)

## 解压后复核

1. 将包解压至新的可写目录，在该目录运行以下命令。

       powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\校验文件.ps1

2. 将示例路径换成固定 EXE 的实际路径，核对演示原包、候选与十步计划。此步无需模拟器。

       powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\复测样本.ps1 -LocalBuild -App "..\Lazarus-Agent.exe" -Sample 工作台演示 -CheckOnly

3. 使用同版工作台准备 Android 16，关闭窗口后去掉 -CheckOnly 运行设备复测。请先关闭正在使用同一专用模拟器的其他任务。其他正例和矛盾断言反例见[样本复测说明](复核说明.md)。

Windows 隔离安装已在现有系统验证。干净 Windows 首次部署和非开发者独立试用仍待验证。现有 Android 结果对应 Android 16 x86_64 专用模拟器。
""".encode()
    files["SHA256SUMS.txt"] = "".join(
        f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in sorted(files.items())
    ).encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent, prefix="f01-local-review-") as temporary:
        staged = Path(temporary) / "review.zip"
        with ZipFile(staged, "x", ZIP_DEFLATED) as archive:
            for name, data in sorted(files.items()):
                archive.writestr(ZipInfo(name), data, compress_type=ZIP_DEFLATED)
        result = verify(staged)
        if (result["local_review"] is None or len(result["samples"]) != len(checked["samples"]) + 1
                or staged.stat().st_size > 100 * 1024 * 1024):
            raise ValueError("本地评审包的身份、样本或大小不符")
        staged.rename(output)
    with output.open("rb") as archive:
        archive_hash = hashlib.file_digest(archive, "sha256").hexdigest()
    return {"archive_sha256": archive_hash, "bytes": output.stat().st_size,
            "file_count": result["file_count"], "local_review": result["local_review"],
            "regression_samples": len(checked["samples"]), "counterexamples": len(result["counterexamples"]),
            "exe_sha256": status["exe_sha256"]}


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    for field in ("materials", "screen", "output"):
        parser.add_argument("--" + field, type=Path, required=True)
    paths = {key: value.resolve() for key, value in vars(parser.parse_args()).items()}
    print(json.dumps(build(**paths), ensure_ascii=False, indent=2))
