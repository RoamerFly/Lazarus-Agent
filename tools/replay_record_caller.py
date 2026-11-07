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
DEFAULT_AUTHORITY = "org.ggbond.archivereader.records"
FOREIGN_PROVIDER = "content://org.ggbond.recordcaller.records"
RECORD_URI = re.compile(r"(?:content|ggbond-record)://[a-zA-Z0-9_.-]+/[a-zA-Z0-9_./-]+")
OUTCOMES = {
    (TARGET, "Shared"): "forged-success",
    (TARGET, "Cannot read record"): "target-rejected",
    (TARGET, "Unexpected file"): "target-rejected",
    (TARGET, "Rejected"): "target-rejected",
    (CALLER, "Caller rejected"): "caller-rejected",
}


def observe_outcome(caller):
    observed = wait_for(caller, {text for _, text in OUTCOMES})
    outcomes = {OUTCOMES[node] for node in observed if node in OUTCOMES}
    if len(outcomes) != 1:
        raise ValueError("The observed response has ambiguous or unexpected ownership")
    outcome, = outcomes
    return {"outcome": outcome, "observed": observed}


def send_probe(caller, *extras):
    for package in (TARGET, CALLER):
        bench.shell(caller.serial, "am", "force-stop", package)
    bench.shell(caller.serial, "am", "start", "-W", "-n", CALLER + "/.BoundaryActivity", *extras)
    return observe_outcome(caller)


def send_known_uri(caller, record_uri):
    response = send_probe(caller, "--es", "record_uri", record_uri)
    if response["outcome"] == "forged-success":
        response["outcome"] = "external-record-status"
    return response


def replay(device, apk, expected, probe, *, uri_after_share=False, compare_uri=False,
           record_path="/record.txt", record_uri=None, forged_bytes_after_share=False):
    if forged_bytes_after_share and not compare_uri:
        raise ValueError("The forged-bytes follow-up requires a before-and-after comparison")
    if record_path not in RECORD_PATHS:
        raise ValueError("Unknown record path for this fixture")
    if record_uri is None:
        record_uri = f"content://{DEFAULT_AUTHORITY}{record_path}"
    if not RECORD_URI.fullmatch(record_uri):
        raise ValueError("Expected a supported record URI with a literal authority and path")
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
        before_share = send_known_uri(caller, record_uri)
        for step in ({"action": "cold_start"}, {"action": "tap", "value": "Share local record"},
                     {"action": "expect", "value": "Shared"}):
            evidence = dict(step)
            _step(target, step, evidence, handled)
            evidence["status"] = "passed"
            in_app_share.append(evidence)
    use_uri = uri_after_share or compare_uri
    response = (send_known_uri(caller, record_uri) if use_uri
                else send_probe(caller, "--ez", "probe_bytes", "true"))
    result = {"api": 36, "abis": target_install["abis"], "candidate_and_installed_sha256": expected,
              "caller_apk_sha256": probe_hash, "uids": uids, **response,
              "input": "known-record-uri-after-share" if use_uri else "external-bytes-before-share"}
    if before_share is not None:
        result["before_share"] = before_share
        result["in_app_share"] = in_app_share
    if use_uri:
        result["record_uri"] = record_uri
    if forged_bytes_after_share:
        result["forged_bytes_after"] = send_probe(caller, "--ez", "probe_bytes", "true")
    return result


def replay_foreign_provider(device, apk, expected, probe):
    result = replay(device, apk, expected, probe, compare_uri=True, record_uri=FOREIGN_PROVIDER + "/record")
    caller = AgentDevice(device, "f01-record-caller", 36, bench.TOOLS, print)
    caller.serial = device.serial
    result["alternate_foreign_uri"] = FOREIGN_PROVIDER + "/other"
    result["alternate_foreign_value"] = send_known_uri(caller, result["alternate_foreign_uri"])
    result["input"] = "foreign-provider-byte-42-before-and-after-share"
    return result


def explicitly_rejected(row):
    """Recognize only an explicit rejection displayed by the target."""
    outcomes = {tuple(node) for node in row["observed"]}.intersection(OUTCOMES)
    return row["outcome"] == "target-rejected" and outcomes == {(TARGET, "Rejected")}


def replay_private_boundary(device, apk, expected, probe):
    fixed = replay(device, apk, expected, probe, compare_uri=True, forged_bytes_after_share=True)
    foreign = replay_foreign_provider(device, apk, expected, probe)
    observed = (fixed["before_share"], fixed, fixed["forged_bytes_after"],
                foreign["before_share"], foreign, foreign["alternate_foreign_value"])
    passed = all(explicitly_rejected(row) for row in observed)
    return {"status": "boundary-verified" if passed else "boundary-failed", "network_model_calls": 0,
            "candidate_sha256": expected, "fixed_probe_uri": fixed, "foreign_provider": foreign}


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
    uri.add_argument("--foreign-provider", action="store_true",
                     help="Replay two single-byte records, with values 42 and 41, from a separate app's provider")
    uri.add_argument("--private-boundary", action="store_true",
                     help="Check the fixed URI, forged bytes, and readable records from another UID")
    parser.add_argument("--forged-bytes-after-share", action="store_true",
                        help="After the URI comparison, send a forged record extra from the same external UID")
    address = parser.add_mutually_exclusive_group()
    address.add_argument("--record-path", choices=RECORD_PATHS)
    address.add_argument("--record-uri", help="Exact content or ggbond-record URI observed in the synthetic candidate")
    args = parser.parse_args()
    if args.record_uri and (not RECORD_URI.fullmatch(args.record_uri)
                            or not (args.uri_after_share or args.uri_before_and_after_share)):
        parser.error("--record-uri requires a supported literal record URI and a URI replay mode")
    if args.forged_bytes_after_share and not args.uri_before_and_after_share:
        parser.error("--forged-bytes-after-share requires --uri-before-and-after-share")
    if (args.foreign_provider or args.private_boundary) and args.record_path:
        parser.error("These probe modes use their own fixed paths")
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
        if args.private_boundary:
            result = replay_private_boundary(device, args.apk, args.sha256, probe)
        elif args.foreign_provider:
            result = replay_foreign_provider(device, args.apk, args.sha256, probe)
        else:
            result = replay(device, args.apk, args.sha256, probe, uri_after_share=args.uri_after_share,
                            compare_uri=args.uri_before_and_after_share,
                            record_path=args.record_path or RECORD_PATHS[0], record_uri=args.record_uri,
                            forged_bytes_after_share=args.forged_bytes_after_share)
    finally:
        device.stop()
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(result.get("status", result.get("outcome")), flush=True)
    return int(result.get("status") == "boundary-failed")


if __name__ == "__main__":
    raise SystemExit(main())
