"""Replay a forged record extra from a separate app on the synthetic P06 fixture."""
import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bench_device as bench
from agent_acceptance import _step, _wait_for_launch_ui
from agent_device import AgentDevice
from agent_workspace import digest_file
from diagnose import inspect_package
from tools.replay_intent_boundary import TARGET, build_probe, wait_for


CALLER = "org.ggbond.recordcaller"
OUTCOMES = {
    (TARGET, "Shared"): "forged-success",
    (TARGET, "Cannot read record"): "target-rejected",
    (TARGET, "Unexpected file"): "target-rejected",
    (CALLER, "Caller rejected"): "caller-rejected",
}


def replay(device, apk, expected, probe, *, uri_after_share=False):
    owner = "f01-record-caller"
    target = AgentDevice(device, owner, 36, bench.TOOLS, print)
    target_install = target.test(apk, expected)
    handled = set()
    if (target_install["installed_sha256"] != expected or "x86_64" not in target_install["abis"]
            or not _wait_for_launch_ui(target, {}, handled)):
        raise ValueError("Could not establish the installed target and its launcher")
    if uri_after_share:
        _step(target, {"action": "tap", "value": "Share local record"}, {}, handled)
        _step(target, {"action": "expect", "value": "Shared"}, {}, handled)
    caller = AgentDevice(device, owner, 36, bench.TOOLS, print)
    probe_hash = digest_file(probe)
    if caller.test(probe, probe_hash, capture_ui=False)["installed_sha256"] != probe_hash:
        raise ValueError("Could not verify the separate caller's installed APK")
    if caller.serial != target.serial:
        raise ValueError("Target and caller are not on the same device")
    uids = {}
    for package in (TARGET, CALLER):
        output = bench.shell(target.serial, "pm", "list", "packages", "-U", package)
        match = re.fullmatch(rf"package:{re.escape(package)} uid:(\d+)", output)
        if not match:
            raise ValueError(f"Could not read UID for {package}")
        uids[package] = int(match[1])
    if len(set(uids.values())) != 2:
        raise ValueError("The probe must run with its own UID")

    if uri_after_share:
        for package in (TARGET, CALLER):
            bench.shell(caller.serial, "am", "force-stop", package)
        bench.shell(caller.serial, "am", "start", "-W", "-n", CALLER + "/.BoundaryActivity",
                    "--ez", "probe_record_uri", "true")
    observed = wait_for(caller, {text for _, text in OUTCOMES})
    outcomes = {OUTCOMES[node] for node in observed if node in OUTCOMES}
    if len(outcomes) != 1:
        raise ValueError("The observed response has ambiguous or unexpected ownership")
    outcome, = outcomes
    if uri_after_share and outcome == "forged-success":
        outcome = "external-record-status"
    return {"api": 36, "abis": target_install["abis"], "candidate_and_installed_sha256": expected,
            "caller_apk_sha256": probe_hash, "uids": uids, "observed": observed, "outcome": outcome,
            "input": "known-record-uri-after-share" if uri_after_share else "external-bytes-before-share"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--uri-after-share", action="store_true",
                        help="After a legitimate share, send its known URI from the separate app")
    args = parser.parse_args()
    out = args.out.resolve()
    if not out.is_relative_to(ROOT / "build"):
        parser.error("Store probe evidence under the ignored build directory")
    if (not re.fullmatch(r"[a-f0-9]{64}", args.sha256) or digest_file(args.apk) != args.sha256
            or inspect_package(args.apk, bench.TOOLS)["package"] != TARGET):
        parser.error("This probe expects the pinned synthetic P06 archive reader APK")
    if not bench.avd_ready(36):
        parser.error("Prepare the Android 16 x86_64 test AVD first")
    out.mkdir(parents=True, exist_ok=False)
    probe = out / "caller.apk"
    build_probe(probe, ROOT / "tools/fixtures/record-caller")
    device = bench.Device(allow_existing=False)
    try:
        result = replay(device, args.apk, args.sha256, probe, uri_after_share=args.uri_after_share)
    finally:
        device.stop()
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(result["outcome"], flush=True)


if __name__ == "__main__":
    main()
