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
RECORD_PATHS = ("/record.txt", "/record")
OUTCOMES = {
    (TARGET, "Shared"): "forged-success",
    (TARGET, "Cannot read record"): "target-rejected",
    (TARGET, "Unexpected file"): "target-rejected",
    (CALLER, "Caller rejected"): "caller-rejected",
}


def observe_outcome(caller):
    observed = wait_for(caller, {text for _, text in OUTCOMES})
    outcomes = {OUTCOMES[node] for node in observed if node in OUTCOMES}
    if len(outcomes) != 1:
        raise ValueError("The observed response has ambiguous or unexpected ownership")
    outcome, = outcomes
    return {"outcome": outcome, "observed": observed}


def send_known_uri(caller, record_path):
    for package in (TARGET, CALLER):
        bench.shell(caller.serial, "am", "force-stop", package)
    bench.shell(caller.serial, "am", "start", "-W", "-n", CALLER + "/.BoundaryActivity",
                "--ez", "probe_record_uri", "true", "--es", "record_path", record_path)
    response = observe_outcome(caller)
    if response["outcome"] == "forged-success":
        response["outcome"] = "external-record-status"
    return response


def replay(device, apk, expected, probe, *, uri_after_share=False, compare_uri=False, record_path="/record.txt"):
    if record_path not in RECORD_PATHS:
        raise ValueError("Unknown record path for this fixture")
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

    before_share = None
    in_app_share = []
    if compare_uri:
        before_share = send_known_uri(caller, record_path)
        for step in ({"action": "cold_start"}, {"action": "tap", "value": "Share local record"},
                     {"action": "expect", "value": "Shared"}):
            evidence = dict(step)
            _step(target, step, evidence, handled)
            in_app_share.append(evidence)
    use_uri = uri_after_share or compare_uri
    response = send_known_uri(caller, record_path) if use_uri else observe_outcome(caller)
    result = {"api": 36, "abis": target_install["abis"], "candidate_and_installed_sha256": expected,
              "caller_apk_sha256": probe_hash, "uids": uids, **response,
              "input": "known-record-uri-after-share" if use_uri else "external-bytes-before-share"}
    if before_share is not None:
        result["before_share"] = before_share
        result["in_app_share"] = in_app_share
    if use_uri:
        result["record_uri"] = f"content://org.ggbond.archivereader.records{record_path}"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--out", type=Path, required=True)
    uri = parser.add_mutually_exclusive_group()
    uri.add_argument("--uri-after-share", action="store_true",
                     help="After a legitimate share, send its known URI from the separate app")
    uri.add_argument("--uri-before-and-after-share", action="store_true",
                     help="Send the same known URI before and after the target writes its record")
    parser.add_argument("--record-path", choices=RECORD_PATHS, default=RECORD_PATHS[0])
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
        result = replay(device, args.apk, args.sha256, probe, uri_after_share=args.uri_after_share,
                        compare_uri=args.uri_before_and_after_share, record_path=args.record_path)
    finally:
        device.stop()
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(result["outcome"], flush=True)


if __name__ == "__main__":
    main()
