"""Run fresh acceptance tasks through the local workbench; only Python stdlib is required."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_TASK = {"tap": "Open archived record", "expect": "Read OK: HTTP/1.1"}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def check_signature_conflict(output, report):
    sys.path.insert(0, str(ROOT))
    from bench_device import ADB, PORT
    serial="emulator-"+str(PORT[30])
    def adb(*args):
        return subprocess.run([str(ADB),"-s",serial,*map(str,args)],capture_output=True,timeout=100)
    require(adb("emu","avd","name").stdout.splitlines()[0]==b"ReviverBench30","Unexpected AVD")
    require(report["tests"][-1]["api"]==30 and report["tests"][-1]["install"]["exit"]==0,
            "Original must be normally installed on the dedicated legacy AVD")
    def installed_hash():
        result=adb("shell","pm","path","org.openintents.notepad")
        paths=result.stdout.decode().strip().splitlines()
        require(result.returncode==0 and len(paths)==1 and paths[0].startswith("package:/data/app/"),
                "Unexpected installed package path")
        result=adb("exec-out","cat",paths[0].removeprefix("package:"))
        require(result.returncode==0 and result.stdout.startswith(b"PK"),"Could not read installed APK")
        return hashlib.sha256(result.stdout).hexdigest()
    before=installed_hash()
    require(before==report["package"]["sha256"], "Installed original does not match input")
    candidate=ROOT/"artifacts/candidates/OI-Notepad-1.3-api35.apk"
    proc=adb("install","-r",candidate)
    message=(proc.stdout+proc.stderr).decode("utf-8","replace").replace(str(candidate),candidate.name)
    after=installed_hash()
    record={"api":30,"serial":serial,"before":before,"after":after,
            "candidate_sha256":hashlib.sha256(candidate.read_bytes()).hexdigest(),
            "exit":proc.returncode,"output":message}
    (output/"signature-conflict.json").write_text(json.dumps(record,indent=2),encoding="utf-8")
    require(proc.returncode!=0 and "INSTALL_FAILED_UPDATE_INCOMPATIBLE" in message and after==before,
            "Expected signature mismatch with original unchanged")


def replay(case, api, port, directory):
    base = f"http://127.0.0.1:{port}"
    token = ""

    def request(path, data=None, content_type="application/json"):
        headers = {"x-reviver-token": token, "Content-Type": content_type}
        with urlopen(Request(base+path, data=data, headers=headers), timeout=90) as response:
            return response.read()

    config = json.loads(request("/api/config"))
    require(not config["model"], "This is a no-model replay; use a rules-only bench.")
    require(config["targets"].get(str(api)), f"API {api} system image unavailable")
    token = config["token"]
    if case=="pacific":
        require(api==36,"The Pacific Rim training recipe is verified on API 36.")
        source=ROOT/"old_apk/环太平洋.apk"
        task=None
    elif case in ("notes","signature-conflict"):
        source = ROOT/"old_apk/OI-Notepad-1.3.apk"
        task = None
    elif case=="file-share":
        source=ROOT/"fixtures/archive-reader/archive-reader-file-share.apk"
        task={"tap":"Share local record","expect":"Shared"}
    else:
        variant = "broken" if case in ("archive", "patched-task-fails") else "control"
        source = ROOT/f"fixtures/archive-reader/archive-reader-{variant}.apk"
        task = dict(ARCHIVE_TASK)
        if case in ("wrong-expectation", "patched-task-fails"):
            task["expect"] = "Expected text deliberately absent"
    original = source.read_bytes()
    job = json.loads(request(f"/api/import/{api}", original, "application/octet-stream"))
    run_path = "/api/jobs/"+job["id"]
    print(f"{case} | API {api} | {job['id']} | {directory}", flush=True)
    action="legacy" if case=="signature-conflict" else "repair"
    request(run_path+"/action/"+action, json.dumps({"task":task}).encode())
    deadline = time.monotonic()+(3600 if case=="pacific" else 480)
    while True:
        job = json.loads(request(run_path))
        if job["status"] != "running":
            break
        if time.monotonic() >= deadline:
            raise TimeoutError("Observation timed out; inspect the existing job at "+base+run_path)
        time.sleep(2)
    raw_report = request(run_path+"/export/report")
    (directory/"report.json").write_bytes(raw_report)
    report = json.loads(raw_report)
    if case=="file-share":
        collect_share_evidence(report,directory)
    share_failure=case=="file-share" and api in (35,36)
    require(report["status"]==("error" if case=="patched-task-fails" or share_failure else "ready"),
            "Unexpected final state; see report.json")
    require(report["api"]==api and report["package"]["sha256"]==hashlib.sha256(original).hexdigest(),
            "Report does not match the requested target or source APK")
    if case=="signature-conflict":
        check_signature_conflict(directory,report)
        print("PASS",flush=True)
        return
    advice = report["diagnosis"]["advice"]
    require(advice["source"]=="rules" and advice["evidence_ids"], "Missing offline decision evidence")
    if case=="pacific":
        from verify_pacific_trial import audit_web
        require(report.get("export") and report.get("training"), "Training did not pass; see report.json")
        jobs=Path(os.environ.get("REVIVER_JOBS",str(Path(os.environ["LOCALAPPDATA"])/"LegacyApkReviver/jobs")))
        name=report["training"]["evidence_folder"]
        require(re.fullmatch(r"[a-f0-9]{32}",job["id"]) and re.fullmatch(r"training-[a-f0-9]{10}",name),
                "Unexpected training evidence path")
        shutil.copytree(jobs/job["id"]/name,directory/name)
        with urlopen(base+run_path+"/export/apk",timeout=120) as response, (directory/"export.apk").open("wb") as output:
            shutil.copyfileobj(response,output)
        audit_web(directory/"report.json",directory/"export.apk")
        print("PASS",flush=True)
        return
    first = report["tests"][0]
    if share_failure:
        candidate=report["tests"][-1]
        require("INSTALL_FAILED_DEPRECATED_SDK_VERSION" in first["install"]["output"] and
                report["patch"]["after"]==24 and candidate["install"]["exit"]==0 and
                candidate["functional_test"].get("exception")=="android.os.FileUriExposedException" and
                report["candidate_diagnosis"]["advice"]["action"]=="report-limit" and report["export"] is None,
                "Expected installation repair followed by an independently evidenced sharing failure")
    if case in ("wrong-expectation", "patched-task-fails") or share_failure:
        require(report["tests"][-1]["functional_test"]["result"]!="passed" and report["export"] is None,
                "Failed task unexpectedly allowed export")
        if case=="patched-task-fails":
            require(report["patch"]["rule"]=="platform-http-library" and
                    report["tests"][-1]["install"]["exit"]==0 and
                    report["tests"][-1]["functional_test"]["result"]!="passed",
                    "Expected an installed but unaccepted candidate")
        try:
            request(run_path+"/export/apk")
        except HTTPError as exc:
            require(exc.code==409, f"Unexpected export status: {exc.code}")
        else:
            raise RuntimeError("Download endpoint returned a package after failure")
    else:
        require(report["acceptance"]["result"]=="passed", "Functional test missing or failed")
        payload = request(run_path+"/export/apk")
        sha = hashlib.sha256(payload).hexdigest()
        require(sha==report["export"]["sha256"], "Downloaded APK hash mismatch")
        certs=report["export"]["signer_certificates_sha256"]
        require(certs and all(len(c)==64 for c in certs), "Missing output signer fingerprint")
        if case=="file-share":
            require(report["patch"]["after"]==23 and "INSTALL_FAILED_DEPRECATED_SDK_VERSION" in first["install"]["output"]
                    and report["acceptance"]["message"].endswith("Shared"),"API 34 share control missing")
        elif case == "notes":
            require("INSTALL_FAILED_DEPRECATED_SDK_VERSION" in first["install"]["output"]
                    and report["acceptance"]["installed_sha256"]==sha
                    and report["acceptance"]["steps"]==["01-entered","02-saved","03-reopened"],
                    "Missing install failure or cold-start evidence")
        elif case == "archive":
            require(first["functional_test"].get("missing_class")=="org/apache/http/ProtocolVersion"
                    and report["patch"]["added_library"]=="org.apache.http.legacy"
                    and ARCHIVE_TASK["expect"] in report["acceptance"]["message"], "Missing crash or repair evidence")
        else:
            require(payload==original, "Control APK unexpectedly changed")
            require(certs==report["package"]["signer_certificates_sha256"], "Control signers unexpectedly changed")
        (directory/"export.apk").write_bytes(payload)
    print("PASS", flush=True)


def collect_share_evidence(report,directory):
    """Keep the screenshots, UI dump and crash trace beside this local acceptance report."""
    jobs=Path(os.environ.get("REVIVER_JOBS",str(Path(os.environ["LOCALAPPDATA"])/"LegacyApkReviver/jobs")))
    require(re.fullmatch(r"[0-9a-f]{32}",report["id"]), "Unexpected job ID")
    probe=report["tests"][-1]["functional_test"]["evidence_folder"]
    require(re.fullmatch(r"probe-[0-9a-f]{10}",probe),"Unexpected evidence folder")
    source=jobs/report["id"]/probe
    require(source.is_dir(),"Set REVIVER_JOBS to the directory used by the local workbench")
    target=directory/probe
    target.mkdir(exist_ok=True)
    for name in ("before.xml","before.png","after-legacy-notice.xml","after-legacy-notice.png","after.xml","after.png","crash.txt"):
        if (source/name).is_file():
            shutil.copyfile(source/name,target/name)


if __name__=="__main__":
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--case",choices=("notes","archive","control","wrong-expectation","patched-task-fails","signature-conflict","file-share","pacific"),required=True)
    cli.add_argument("--api",choices=(34,35,36),type=int,default=35)
    cli.add_argument("--port",type=int,default=8767)
    cli.add_argument("--output",type=Path,default=Path("replay-results"))
    args=cli.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    run_dir=Path(tempfile.mkdtemp(prefix=f"{args.case}-{args.api}-",dir=args.output))
    replay(args.case,args.api,args.port,run_dir)
