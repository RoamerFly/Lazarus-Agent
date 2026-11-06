"""Replay the same-app record handoff with a competing app on a disposable AVD."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bench_device as bench
from agent_acceptance import _read
from agent_device import AgentDevice
from agent_workspace import digest_file
from diagnose import inspect_package
from offline_kit import java_environment
from reviver import _tool_temp_root, run, test_key

TARGET = "org.ggbond.archivereader"
PROBE = "org.ggbond.intentboundaryprobe"
ACTION = TARGET + ".OPEN_RECORD"
URI = "content://org.ggbond.archivereader.records/record.txt"
MIME = "application/vnd.ggbond.record"
LABEL = "Intent Boundary Probe"
DENIED_READS = {"PROBE_ACCESS_DENIED", "PROBE_DATA_UNAVAILABLE"}


def build_probe(out, fixture=ROOT / "tools/fixtures/intent-boundary"):
    platform = sorted(bench.SDK.glob("platforms/android-*/android.jar"), reverse=True)[0]
    environment = java_environment(os.environ.copy())
    key = test_key(environment)
    with tempfile.TemporaryDirectory(dir=_tool_temp_root(), prefix="intent-probe-") as temporary:
        work = Path(temporary)
        classes = work / "classes"
        classes.mkdir()
        for name in ("AndroidManifest.xml", "BoundaryActivity.java"):
            shutil.copyfile(fixture / name, work / name)
        run([bench.TOOLS / "aapt2.exe", "link", "-I", platform, "--manifest",
             "AndroidManifest.xml", "-o", "base.apk"], cwd=work)
        run(["javac", "-encoding", "UTF-8", "-source", "8", "-target", "8", "-classpath",
             platform, "-d", classes, work / "BoundaryActivity.java"], environment)
        run([bench.TOOLS / "d8.bat", "--min-api", "21", "--lib", platform, "--output", work,
             *classes.rglob("*.class")], environment)
        with ZipFile(work / "base.apk", "a") as archive:
            archive.write(work / "classes.dex", "classes.dex")
        run([bench.TOOLS / "zipalign.exe", "-f", "4", "base.apk", "aligned.apk"], cwd=work)
        run(["java", "-jar", bench.TOOLS / "lib/apksigner.jar", "sign", "--ks", key,
             "--ks-pass", "env:REVIVER_KEYPASS", "--v4-signing-enabled", "false",
             "--out", out, work / "aligned.apk"], environment)


def snapshot(tester):
    return [(n["package"], n["text"] or n["content-desc"] or n["resource-id"])
            for n in tester.observe()["nodes"]]


def wait_for(tester, labels):
    for _ in range(8):
        observed = snapshot(tester)
        if labels.intersection(label for _, label in observed):
            return observed
        time.sleep(.5)
    raise RuntimeError(f"Expected {labels!r}, last device observation {observed!r}")


def assert_ungranted(tester):
    bench.shell(tester.serial, "am", "start", "-W", "-n", PROBE + "/.BoundaryActivity", "-d", URI)
    observed = wait_for(tester, DENIED_READS | {"PROBE_READ_42"})
    if ((PROBE, "PROBE_GRANT_DENIED") not in observed
            or not any(package == PROBE and label in DENIED_READS for package, label in observed)):
        raise RuntimeError(f"Expected the independent UID to be denied an ungranted read: {observed!r}")
    return observed


def observe(device, apk, probe, expected, *, implicit):
    facts = inspect_package(apk, bench.TOOLS)
    if facts["package"] != TARGET or facts["launcher"] != TARGET + ".ShareActivity":
        raise ValueError("This replay only accepts the team-owned archive-reader share sample")
    if facts["sha256"] != expected:
        raise ValueError("Candidate differs from the pinned test input")
    tester = AgentDevice(device, "intent-boundary", 36, bench.TOOLS, print)
    companion = tester.test(probe, digest_file(probe), capture_ui=False)
    if companion["installed_sha256"] != digest_file(probe):
        raise RuntimeError("Probe installation identity could not be verified")
    test = tester.test(apk, expected)
    if test["installed_sha256"] != expected or "x86_64" not in test["abis"]:
        raise RuntimeError("Unexpected candidate or device identity")
    launch_observations = []
    if not _read(tester, set(), launch_observations):
        raise RuntimeError("Candidate UI was not available after its recognized startup prompts")
    serial = tester.serial
    uids = {}
    for name in (TARGET, PROBE):
        listing = bench.shell(serial, "pm", "list", "packages", "-U", name)
        match = re.fullmatch(rf"package:{re.escape(name)} uid:(\d+)", listing)
        if not match:
            raise RuntimeError(f"Missing app UID for {name}: {listing}")
        uids[name] = int(match[1])
    if uids[TARGET] == uids[PROBE]:
        raise RuntimeError("Probe must be installed under a distinct UID")
    handlers = bench.shell(serial, "cmd", "package", "query-activities", "--brief",
                           "-a", ACTION, "-d", URI, "-t", MIME)
    if any(name + "/" not in handlers for name in uids):
        raise RuntimeError("Both competing activities must match the same implicit Intent")

    denied = assert_ungranted(tester)
    tester.action("cold_start")
    tester.action("tap_text", "Share local record")
    chooser = []
    if implicit:
        chooser = wait_for(tester, {LABEL, "PROBE_READ_42"})
        if (PROBE, "PROBE_READ_42") not in chooser:
            tester.action("tap_text", LABEL)
            opened = wait_for(tester, {"Just once", "JUST ONCE", "PROBE_READ_42"})
            for _, label in opened:
                if label in {"Just once", "JUST ONCE"}:
                    tester.action("tap_text", label)
                    break
    result = wait_for(tester, {"PROBE_READ_42"} if implicit else {"Shared"})
    wanted = (PROBE, "PROBE_READ_42") if implicit else (TARGET, "Shared")
    if wanted not in result or (implicit and (PROBE, "PROBE_GRANT_ALLOWED") not in result):
        raise RuntimeError(f"Unexpected package identity for {wanted}")
    after_scoped_direct = None if implicit else assert_ungranted(tester)
    return {"candidate_sha256": expected, "installed_sha256": test["installed_sha256"],
            "uids": uids, "abis": test["abis"], "implicit_handlers": handlers, "direct_probe": denied,
            "launch_observations": launch_observations, "chooser": chooser, "after_share": result,
            "after_scoped_direct": after_scoped_direct}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--implicit", type=Path, required=True)
    parser.add_argument("--implicit-sha", required=True)
    parser.add_argument("--scoped", type=Path, required=True)
    parser.add_argument("--scoped-sha", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    if not out.is_relative_to(ROOT / "build"):
        parser.error("Save raw results under the project's ignored build directory")
    out.mkdir(parents=True, exist_ok=False)
    probe = out / "intent-boundary-probe.apk"
    build_probe(probe)
    if not bench.avd_ready(36):
        raise RuntimeError("Prepare the Android 16 x86_64 AVD before replaying")
    # Use separate disposable emulator sessions so no grant survives between cases.
    for label, implicit in (("implicit", True), ("scoped", False)):
        device = bench.Device(allow_existing=False)
        try:
            row = observe(device, getattr(args, label), probe, getattr(args, label + "_sha"), implicit=implicit)
            evidence = {"api": 36, "probe_sha256": digest_file(probe), **row}
            (out / (label + ".json")).write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
            print(label, evidence["candidate_sha256"], flush=True)
        finally:
            device.stop()


if __name__ == "__main__":
    main()
