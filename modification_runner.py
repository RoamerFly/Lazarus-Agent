"""Execute one reviewed capability against immutable APKs in the private lab."""
import hashlib
import re
import uuid

import bench_device
from compatibility_matrix import finish_matrix, new_matrix, set_row
from repair_history import add_message, archive_version


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def capability(kind):
    if kind == "remember-file":
        import remember_file
        from modification_probe import verify_behavior
        return remember_file, verify_behavior
    if kind == "localize-zh":
        import localization_edit
        from localization_probe import verify_locale
        return localization_edit, verify_locale
    raise ValueError("未知改造工具")


def evidence_install(device, owner, api, facts, apk, log):
    serial = device.ensure(owner, api, log, facts["native_abis"])
    launch = device.install_run(apk, facts)
    if launch["install"]["exit"] or launch.get("launch", {}).get("exit") != 0:
        raise ValueError("基准或候选 APK 无法正常安装启动，请先做兼容修复并选择已验证版本。")
    package = facts["package"]
    lines = bench_device.shell(serial, "pm", "path", package).splitlines()
    if len(lines) != 1 or not re.fullmatch(r"package:/data/app/[\w./=+~-]+/base\.apk", lines[0]):
        raise ValueError("无法核对设备上的单个 APK")
    installed = bench_device.shell(serial, "sha256sum", "'" + lines[0][8:] + "'", timeout=120).split()[0]
    if installed != digest(apk):
        raise ValueError("实际安装的 APK 与本轮输入哈希不符")
    return serial, {"installed_sha256": installed, "verified_environment": {
        "sdk": api, "supported_abis": launch["abis"],
        "type": "redroid-arm-lab" if serial == bench_device.ARM_LAB else "android-emulator"}}


def execute(folder, state, device, persist, signatures, now):
    from apk_edit_build import build_modified
    change = state["modification"]
    plan = change["plan"]
    run = change["runs"][-1]
    matrix = state["compatibility"]
    directory = folder / run["directory"]
    directory.mkdir(parents=True, exist_ok=False)
    primary = max(plan["targets"])
    source = (folder / "original.apk" if plan["base_version"] is None else
              folder / "versions" / f"{plan['base_version']}.apk")
    facts = state["package"]
    adapter, probe = capability(plan["kind"])

    def log(message):
        change["message"] = str(message)[:1200]
        state["events"].append({"time":now(), "text":change["message"]})
        persist(folder, state)

    def observe(path, api, label):
        log(f"Android {api-20} · {label}")
        serial, proof = evidence_install(device, folder.name, api, facts, path, log)
        dest = directory / f"{label}-{api}"
        check = probe(serial, facts, plan["criteria"], dest)
        return {**proof, **check, "evidence": dest.relative_to(folder).as_posix()}

    if digest(source) != plan["source_sha256"]:
        raise ValueError("基准文件与计划哈希不符，请重新制定计划")
    baseline = observe(source, primary, "基准复现")
    run["baseline"] = baseline
    if baseline["result"] not in {"passed","failed"}:
        raise ValueError(baseline["message"])
    already = baseline["result"] == "passed"
    if already:
        artifact = source
        log("基准已满足主目标，复核所选范围")
    else:
        # A clean rebuild must preserve the observed failure before the edit can take credit.
        control = directory / "control.apk"
        try:
            log("准备已校验的 Apktool 并执行无修改回编对照，首次下载后可离线复用")
            build_modified(source, control, min(plan["targets"]), bench_device.TOOLS, lambda decoded: {"kind":"roundtrip-control"})
            ref = observe(control, primary, "回编对照")
            run["control"] = ref
            # Confirm the same action failed for the same observable reason.
            fields = ("result", "failed_step", "failed_locale", "message")
            if any(ref.get(key) != baseline.get(key) for key in fields):
                raise ValueError("无修改回编改变了基准表现，终止本次改造")
        finally:
            control.unlink(missing_ok=True)

        def transform(decoded):
            inspection = adapter.inspect(decoded, plan["criteria"])
            if not inspection.get("available"):
                raise ValueError(inspection.get("reason","当前 APK 没有适用的修改位置"))
            applied = adapter.apply(decoded, inspection["plan"])
            return {**applied,"kind":plan["kind"]}

        log("检查可修改位置并构建候选 APK")
        artifact = directory / "modified.apk"
        run["build"] = build_modified(source, artifact, min(plan["targets"]), bench_device.TOOLS, transform)
    sha = digest(artifact)
    for api in sorted(plan["targets"], reverse=True):
        set_row(matrix, api, status="running")
        persist(folder,state)
        try:
            proof = baseline if already and api == primary else observe(artifact,api,"候选复测")
            if proof["result"] != "passed":
                set_row(matrix,api,functional=proof)
                raise ValueError(proof["message"])
            set_row(matrix, api, status="passed", installed_sha256=proof["installed_sha256"],
                    verified_environment=proof["verified_environment"], functional=proof,
                    verification_level=plan["description"])
        except Exception as exc:
            set_row(matrix,api,status="failed",failure=str(exc)[:600])
        persist(folder,state)
    main = next(row for row in matrix["rows"] if row["api"] == primary)
    proposal = {"file":artifact.relative_to(folder).as_posix(),"sha256":sha,
                "level":("基准已有此行为 · " if already else "改造行为已验证 · ") + plan["description"],
                "installed_sha256":main.get("installed_sha256"),
                "verified_environment":main.get("verified_environment"),
                "signer_certificates_sha256":signatures(artifact,primary),
                "verified_targets":plan["targets"],"modification_id":plan["id"]}
    if not finish_matrix(matrix,proposal):
        raise ValueError("所选系统未全部通过行为验收，请查看各版本的失败步骤")
    state["export"] = proposal
    version = archive_version(folder,state)
    revisions = version.setdefault("modification_ids", [])
    if plan["id"] not in revisions:
        revisions.append(plan["id"])
    run.update(version=version["number"], sha256=sha, unchanged=already)
    log("选定范围内的验收均已通过，可导出 V%d" % version["number"])


def start_run(state, plan_id):
    change = state.get("modification", {})
    plan = change.get("plan")
    if not plan or plan["id"] != plan_id:
        raise ValueError("该计划已变更，请重新查看当前计划")
    prior = [r for r in change["runs"] if r["plan"]["id"] == plan_id]
    if len(prior) >= 3:
        raise ValueError("本计划已尝试三次，请按失败证据修订需求或验收条件")
    if prior and prior[-1]["status"] == "passed":
        raise ValueError("本计划已通过，请从版本记录下载，或创建新计划")
    run = {"plan":plan.copy(), "status":"running","directory":"modifications/"+uuid.uuid4().hex}
    change["runs"].append(run)
    change.update(status="running", message="开始复现原行为")
    state.update(status="running", action="modify", export=None,
                 compatibility=new_matrix(plan["targets"],max(plan["targets"])))
    state.pop("attempt_feedback",None)
    feedback = state.get("pending_feedback") or {}
    if feedback.get("version") is not None and feedback["version"] == plan["base_version"]:
        state["attempt_feedback"] = feedback["id"]


def run_worker(folder,state,device,persist,signatures,now,lock):
    change=state["modification"]
    run=change["runs"][-1]
    try:
        execute(folder,state,device,persist,signatures,now)
        change["status"]=run["status"]="passed"
        state["status"]="ready"
    except Exception as exc:
        change["status"]=run["status"]="failed"
        change["message"]=run["failure"]=str(exc)[:1200]
        matrix=state["compatibility"]
        for row in matrix["rows"]:
            if row["status"] in {"running","pending"}:
                set_row(matrix,row["api"],status="skipped",failure=change["message"])
        matrix.update(status="failed",artifact=None)
        state.update(status="error",export=None)
        add_message(state,"assistant","本轮改造未通过验收。"+change["message"])
    finally:
        try:
            run["compatibility"]=state["compatibility"]
            state["updated"]=now()
            persist(folder,state)
        finally:
            lock.release()
