"""Run the frozen F01 Android 16 benchmark in a disposable workbench AVD."""
from argparse import ArgumentParser
from contextlib import nullcontext
from datetime import datetime, timezone
from functools import partial
import json
from pathlib import Path
import sys
from time import monotonic
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import auto_translation
import feature_agent
from agent_acceptance import _observe, parse_steps, verified_for, verify
from agent_device import AgentDevice
from apk_edit_build import build_modified
from bench_device import Device, TOOLS, ready
from diagnose import manifest_changes
from model_settings import get_config
from reviver import repair

PROTOCOL = ROOT / "tools/f01-p1-cases.json"
BASE_URL = "https://api.deepseek.com"
CONTROLS = ("roundtrip", "rules", "one-build")
USAGE_FIELDS = ("prompt_tokens", "completion_tokens", "prompt_cache_hit_tokens",
                "prompt_cache_miss_tokens")


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json(path, value):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def digest(path):
    from agent_workspace import digest_file
    return digest_file(path)


def plan(raw_steps):
    return parse_steps({"steps": [
        {"action": entry[0], **({"value": entry[1]} if len(entry) == 2 else {})}
        for entry in raw_steps
    ]})


def trial_schedule(protocol):
    for case in protocol["cases"]:
        if case["class"] == "positive":
            for control in CONTROLS:
                yield case, control, 1
        for repeat in range(1, protocol[case["class"] + "_repetitions"] + 1):
            yield case, "agent", repeat


def one_build_dispatch(dispatch, session, name, args):
    """End on the first build attempt, before the model can see its result."""
    if name not in {"build_apk", "localize_resources"}:
        if name == "finish" and args.get("outcome") == "candidate":
            session.finish("blocked", "The model selected no valid build.")
            return {"finished": True}
        return dispatch(session, name, args)
    try:
        result = dispatch(session, name, args)
        session.finish("candidate", "First build exported for independent comparison.")
        return result
    except (ValueError, TypeError, OSError, RuntimeError):
        session.finish("blocked", "First build failed without model feedback.")
        raise


def rule_build(rule, source, artifact, api):
    if rule == "unavailable":
        return None
    if rule == "remember":
        import remember_file

        def transform(decoded):
            found = remember_file.inspect(decoded)
            if not found["available"]:
                raise ValueError(found["reason"])
            return remember_file.apply(decoded, found["plan"])

        return build_modified(source, artifact, api, TOOLS, transform)
    return repair(source, artifact, api, TOOLS, {
        "floor": "installation-floor", "legacy-http": "platform-http-library",
    }[rule])


def run_trial(case, mode, repeat, api, output, device, config):
    source = ROOT / case["apk"]
    artifact = output / "candidate.apk"
    steps = plan(case["steps"])
    source_hash = digest(source)
    record = {"case": case["id"], "class": case["class"], "mode": mode, "repeat": repeat,
              "api": api, "source_sha256": source_hash, "started": utc_now(),
              "model_usage": [], "build_attempts": 0}
    start = monotonic()
    write_json(output / "started.json", record)

    def progress(message):
        with (output / "progress.log").open("a", encoding="utf-8") as log:
            print(utc_now(), message, file=log)

    def tester(target):
        return AgentDevice(device, "f01-p1", target, TOOLS, progress)

    def usage(raw, model, fingerprint):
        values = {k: v for k, v in (raw or {}).items()
                  if k in USAGE_FIELDS and type(v) is int and v >= 0}
        record["model_usage"].append({"time": utc_now(), "model": model,
                                     "fingerprint": fingerprint, **values})
        write_json(output / "usage.json", record["model_usage"])

    def track_build(session):
        record["build_attempts"] += 1
        return start_build(session)

    start_build = feature_agent.Session.start_build
    try:
        if mode == "roundtrip":
            record["build_attempts"] = 1
            result = build_modified(source, artifact, api, TOOLS,
                                    lambda decoded: {"kind": "roundtrip-control"})
        elif mode == "rules":
            record["build_attempts"] = int(case["rule"] != "unavailable")
            result = rule_build(case["rule"], source, artifact, api)
        else:
            original_dispatch = feature_agent.Session.dispatch

            def first_build(session, name, args):
                return one_build_dispatch(original_dispatch, session, name, args)

            dispatch = patch.object(feature_agent.Session, "dispatch", first_build) if mode == "one-build" else nullcontext()
            with (
                dispatch,
                patch.object(feature_agent.Session, "start_build", track_build),
                patch.object(auto_translation, "get_config", return_value=config),
                patch.object(feature_agent, "_request", partial(feature_agent._request, observe_usage=usage)),
            ):
                result = feature_agent.run(
                    source, artifact, TOOLS, api, source_hash, case["request"], config, progress,
                    tester=tester(api) if mode == "agent" else None, acceptance_steps=steps)
        record["result"] = result
        if result and result.get("sha256"):
            proof = verify(source, source_hash, artifact, result["sha256"], [api], steps, tester, ready, progress)
            try:
                proof["manifest_changes"] = manifest_changes(source, artifact, TOOLS)
            except (OSError, ValueError, RuntimeError) as exc:
                proof["manifest_inspection_error"] = type(exc).__name__
            write_json(output / "acceptance.json", proof)
            record["behavior_verified"] = verified_for(proof, result["sha256"])
            row = proof["rows"][0]
            record["comparison"] = row.get("comparison")
            if row.get("candidate"):
                record["candidate_status"] = {
                    field: row["candidate"][field] for field in ("build", "install", "launch", "function")}
                record["abis"] = (row["candidate"].get("device") or {}).get("abis", [])
        else:
            record["behavior_verified"] = False
            if case["class"] == "negative":
                record["baseline"] = _observe(tester(api), source, source_hash, steps)
    except (OSError, ValueError, RuntimeError) as exc:
        record["error"] = type(exc).__name__
        progress(str(exc).replace(config.get("key") or "\0", "<model-key>")[:800])
    record["duration_seconds"] = round(monotonic() - start, 3)
    write_json(output / "result.json", record)
    return record


def model_config(protocol, credential_file):
    if credential_file:
        # A user-supplied file is used in memory only, and only for the official endpoint.
        return {"url": BASE_URL, "model": protocol["model"],
                "key": credential_file.read_text(encoding="utf-8-sig").strip(), "enabled": True}
    return get_config()


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--credential-file", type=Path)
    parser.add_argument("--only", nargs="+", help="Run a portion of the fixed schedule")
    parser.add_argument("--controls-only", action="store_true", help="Run offline controls only")
    args = parser.parse_args()
    output = args.out.resolve()
    if not output.is_relative_to(ROOT / "build"):
        parser.error("Store raw benchmark evidence under the ignored build/ directory")
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8-sig"))
    identifiers = {case["id"] for case in protocol["cases"]}
    if args.only and not set(args.only) <= identifiers:
        parser.error("Unknown case ID")
    for case in protocol["cases"]:
        plan(case["steps"])
    config = {} if args.controls_only else model_config(protocol, args.credential_file)
    if not args.controls_only and (config.get("url") != BASE_URL or config.get("model") != protocol["model"]
                                   or not config.get("enabled") or not config.get("key")):
        parser.error("Configure the official DeepSeek Flash endpoint and key")
    api = protocol["target"]
    if not ready(api):
        parser.error("Prepare the dedicated Android 16 lab before running")
    output.mkdir(parents=True, exist_ok=True)
    lock = output / "running.lock"
    device = Device()
    with lock.open("x", encoding="utf-8") as owner:
        import os
        owner.write(str(os.getpid()))
    try:
        for case, mode, repeat in trial_schedule(protocol):
            if args.only and case["id"] not in args.only:
                continue
            if args.controls_only and mode not in {"roundtrip", "rules"}:
                continue
            directory = output / f"{case['id']}-{mode}-{repeat}"
            if directory.exists():
                print("retained", directory.name, flush=True)
                continue
            directory.mkdir()
            print("start", utc_now(), directory.name, flush=True)
            record = run_trial(case, mode, repeat, api, directory, device, config)
            print("end", utc_now(), directory.name, record.get("behavior_verified"),
                  record.get("error"), flush=True)
    finally:
        try:
            device.stop()
        finally:
            lock.unlink()


if __name__ == "__main__":
    main()
