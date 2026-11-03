"""Audit the recorded Android 16 ARM32 game trial before copying its standalone APK."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from pacific_game_checkpoints import MATCH_THRESHOLD, ROOT, gray, locate


def read(path):
    return json.loads(path.read_bytes())


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def audit():
    ui = ROOT / "redroid16-game-ui"
    original = read(ROOT / "redroid16-native-arm-original.json")
    boot = read(ROOT / "redroid16-native-arm-boot.json")
    canary = read(ROOT / "redroid16-native-arm-canary.json")
    trial = read(ROOT / "redroid16-native-arm-game.json")
    observation = read(ui / "observations.json")
    patch = read(ROOT / "android16-pic" / "report.json")["native_candidate"]
    candidate = ROOT / "android16-pic" / "experimental.apk"
    digest = sha256(candidate)
    source = ROOT.parents[1] / "old_apk" / "环太平洋.apk"
    require(sha256(source) == original["sha256"] == patch["source_sha256"], "Source hash mismatch")
    require(original["exit"] != 0 and "INSTALL_FAILED_DEPRECATED_SDK_VERSION" in original["output"],
            "Missing original default installation baseline")
    require(boot["sys.boot_completed"] == "1" and boot["ro.build.version.sdk"] == "36"
            and "armeabi-v7a" in boot["ro.product.cpu.abilist32"].split(","), "Wrong environment")
    require(canary.get("passed") and canary["primary_cpu_abi"] == "armeabi-v7a", "Missing JNI control")
    require(trial["install_exit"] == 0 and trial["api"] == 36 and "primaryCpuAbi=armeabi-v7a" in trial["abi"],
            "Missing candidate installation on ARM32")
    require(digest == trial["installed_sha256"] == trial["sha256"] == patch["sha256"]
            == observation["installed_sha256"], "Installed and candidate bytes differ")
    require(observation["package"] == "com.jumpgames.pacificrim" and observation["api"] == 36,
            "Unexpected observed application")
    require(observation["passed"], "Training interaction has not passed")
    check_steps(ui, observation["steps"], observation["package"])
    return candidate, digest


def check_steps(ui, steps, package):
    expected = [("intro", "tap"), ("career", "tap"), ("deploy", "tap"), ("block", "hold"), ("evade", "observed")]
    require([(s["expected"], s["action"]) for s in steps] == expected, "Incomplete game interaction")
    for step in steps:
        stage = step["expected"]
        screenshot = ui / (stage + ".png")
        require(sha256(screenshot) == step["screenshot_sha256"], f"{stage} screenshot hash mismatch")
        require(step["foreground"] == package and locate(gray(screenshot), stage)[0] >= MATCH_THRESHOLD,
                f"{stage} did not match the foreground game")


def audit_web(report, apk):
    record = read(report)
    digest = sha256(apk)
    export = record["export"]
    env = export["verified_environment"]
    original, repaired = record["tests"][0], record["tests"][-1]
    require(record["status"]=="ready" and record["diagnosis"]["advice"]["source"]=="rules", "Offline trial missing")
    require(original["install"]["exit"]!=0 and "INSTALL_FAILED_DEPRECATED_SDK_VERSION" in original["install"]["output"],
            "Original installation was not rejected by the target")
    require(repaired["install"]["exit"]==0 and repaired["launch"]["exit"]==0, "Candidate install or launch failed")
    require(record["api"]==env["sdk"]==36 and env["type"]=="redroid-arm-lab" and env["primary_cpu_abi"]=="armeabi-v7a",
            "Unexpected target environment")
    require(record["package"]["sha256"]==sha256(ROOT.parents[1]/"old_apk/环太平洋.apk"), "Unexpected input")
    require(record["patch"]["sha256"]==export["installed_sha256"]==export["sha256"]==digest, "Candidate hash mismatch")
    require(record["training"]["result"]=="passed" and repaired["training"]==record["training"], "Missing functional trial")
    folder=record["training"]["evidence_folder"]
    require(Path(folder).name==folder and folder.startswith("training-"), "Unexpected evidence directory")
    check_steps(report.parent/folder,record["training"]["steps"],record["package"]["package"])
    return apk, digest


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--web-report", type=Path, help="Verify a live workbench report with the adjacent screenshot folder")
    cli.add_argument("--apk", type=Path, help="Standalone APK downloaded by the browser")
    cli.add_argument("--output", type=Path, help="Copy the verified APK here after a successful audit")
    args = cli.parse_args()
    if bool(args.web_report)!=bool(args.apk):
        cli.error("--web-report and --apk must be supplied together")
    candidate, digest = audit_web(args.web_report,args.apk) if args.web_report else audit()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=args.output.parent, delete=False) as temp:
            stashed = Path(temp.name)
        try:
            shutil.copyfile(candidate, stashed)
            require(sha256(stashed) == digest, "Copy hash mismatch")
            os.replace(stashed, args.output)
        finally:
            stashed.unlink(missing_ok=True)
    print(f"Android 16 / armeabi-v7a / training interaction verified. SHA-256 {digest}")
