"""Run an old APK and its candidate against the same normal-install policy on a lab AVD."""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import time


def trial(adb, serial, apk, candidate, output):
    def call(*args, check=True):
        p = subprocess.run([adb, "-s", serial, *map(str,args)], capture_output=True,
                           encoding="utf-8", errors="replace", timeout=150)
        message = (p.stdout + p.stderr).strip()
        if check and p.returncode:
            raise RuntimeError(message)
        return {"command":list(map(str,args)), "exit":p.returncode, "output":message}

    if not serial.startswith("emulator-"):
        raise ValueError("Use a dedicated Android Emulator")
    device = call("emu", "avd", "name")["output"].splitlines()[0]
    if not device.startswith("Reviver"):
        raise ValueError(f"Not a Reviver lab AVD: {device}")
    if output.exists():
        raise FileExistsError("Use a new evidence file for each run")
    for _ in range(60):
        if call("shell","getprop","sys.boot_completed",check=False)["output"] == "1":
            break
        time.sleep(2)
    else:
        raise RuntimeError("AVD did not finish booting")
    api = call("shell","getprop","ro.build.version.sdk")["output"]
    package = "org.openintents.notepad"
    installed = call("shell","pm","list","packages",package)["output"]
    if package in installed:
        raise ValueError("Use a clean lab profile without OI Notepad installed")
    report = {"utc":datetime.now(timezone.utc).isoformat(),"avd":device,"serial":serial,"api":api,
              "abi":call("shell","getprop","ro.product.cpu.abilist")["output"],
              "fingerprint":call("shell","getprop","ro.build.fingerprint")["output"]}
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        for label, path in (("original",apk),("candidate",candidate)):
            report[label] = {"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),
                             "install":call("install",path,check=False)}
            success = report[label]["install"]["exit"] == 0
            if label == "original" and success:
                break
            if label == "candidate" and success:
                report["launch"] = call("shell","am","start","-W","-n",
                    package+"/.noteslist.NotesList",check=False)
    finally:
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--adb",default="adb")
    parser.add_argument("--serial",required=True)
    for key in ("apk","candidate","output"):
        parser.add_argument("--"+key,required=True,type=Path)
    trial(**vars(parser.parse_args()))
