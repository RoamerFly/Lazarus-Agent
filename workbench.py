"""Loopback-only Android repair workbench, Python 3.11+ / Windows."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import threading
import time
import uuid
from zipfile import ZipFile

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from bench_device import ADB, ARM_LAB, ROOT, TOOLS, Device, invoke, ready, shell
from diagnose import abi_pruning_plan, assess, inspect_package
from model_advisor import advise
from function_probe import (MAX_LIBRARY_JAR_BYTES, discover_shared_library, extract_missing_class,
                            probe, resolve_platform_library)
from model_settings import public_config
from reviver import INSTALL_FLOOR, repair, run
from tools.note_smoke import exercise
from repair_history import add_message, archive_version, record_feedback
from compatibility_matrix import finish_matrix, new_matrix, normalize_targets, set_row
from storage_paths import jobs_dir

OI_HASH="3c5f3ebcbbae199a403304e195b63f29198729467d7900f85c28a18be9695539"
PACIFIC_HASH="9697bb72668ec713bca647b26878210df30c76d65cc7a927d4a1a844591c8180"
STORAGE=jobs_dir()
STORAGE.mkdir(parents=True,exist_ok=True)
TOKEN=secrets.token_urlsafe(32)
INSTANCE=uuid.uuid4().hex
PORT=int(os.environ.get("REVIVER_PORT","8767"))
app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
device=Device()
operation=threading.Lock()
state_lock=threading.RLock()
STATE_IO_ATTEMPTS=6


def now():
    return datetime.now(timezone.utc).isoformat()


@app.middleware("http")
async def local_only(request,call_next):
    hosts={f"127.0.0.1:{PORT}",f"localhost:{PORT}"}
    if request.headers.get("host") not in hosts:
        return Response(status_code=403)
    if request.method=="POST" and not secrets.compare_digest(request.headers.get("x-reviver-token",""), TOKEN):
        return Response(status_code=403)
    response=await call_next(request)
    response.headers["Cache-Control"]="no-store"
    response.headers["X-Content-Type-Options"]="nosniff"
    response.headers["X-Frame-Options"]="DENY"
    return response


def job_dir(job):
    if not re.fullmatch("[a-f0-9]{32}",job) or not (STORAGE/job/"state.json").is_file():
        raise HTTPException(404,"任务不存在")
    return STORAGE/job


def _transient_state_error(exc):
    return isinstance(exc,PermissionError) or (isinstance(exc,OSError) and getattr(exc,"winerror",None) in {5,32})


def _state_retry_pause(attempt):
    time.sleep(0.02*(2**attempt))


def _read_state_file(path):
    return json.loads(path.read_text(encoding="utf-8"))


def read(folder):
    folder=Path(folder)
    last_error=None
    for attempt in range(STATE_IO_ATTEMPTS):
        try:
            # Keep the original global lock, but only around this one short
            # filesystem read. The writer uses an atomic replace.
            with state_lock:
                state=_read_state_file(folder/"state.json")
            return state
        except (OSError,json.JSONDecodeError) as exc:
            # A concurrent Windows replace can briefly deny the read.
            # Invalid JSON is also retried because an external legacy
            # writer may not provide atomic replacement.
            if not (_transient_state_error(exc) or isinstance(exc,json.JSONDecodeError)):
                raise
            last_error=exc
            if attempt+1<STATE_IO_ATTEMPTS:
                _state_retry_pause(attempt)
    raise last_error


def save(folder,state):
    folder=Path(folder)
    payload=json.dumps(state,ensure_ascii=False,indent=2)
    target=folder/"state.json"
    last_error=None
    for attempt in range(STATE_IO_ATTEMPTS):
        # Do not reuse state.tmp: another process can otherwise remove or
        # lock it between write and replacement on Windows.
        temp=folder/(".state-"+str(os.getpid())+"-"+uuid.uuid4().hex+".tmp")
        try:
            with state_lock:
                temp.write_text(payload,encoding="utf-8")
                os.replace(temp,target)
            return
        except OSError as exc:
            last_error=exc
            if not _transient_state_error(exc):
                raise
            if attempt+1<STATE_IO_ATTEMPTS:
                _state_retry_pause(attempt)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
    raise last_error


def signing_fingerprints(apk, api=None):
    options=[] if api is None else ["--min-sdk-version",str(api),"--max-sdk-version",str(api)]
    details=run(["java","-jar",TOOLS/"lib/apksigner.jar","verify",*options,"--print-certs",apk.name],cwd=apk.parent)
    certs=re.findall(r"(?m)^Signer #\d+ certificate SHA-256 digest: ([0-9a-fA-F]{64})\s*$",details)
    if not certs:
        raise ValueError("未验证到 APK 签名证书")
    return [cert.lower() for cert in certs]


def embedded_native_summary(apk):
    """Small UI-safe availability signal; detailed paths stay in the report."""
    try:
        from asset_native_repair import replacement_map
        mapped=replacement_map(apk)
        if not isinstance(mapped,dict) or not mapped:
            return {"available":False}
        return {"available":True,"replacement_count":len(mapped),
                "top_level_libraries":sorted(mapped)}
    except Exception:
        # Import must remain usable for ordinary APKs when no audited payload
        # recipe applies.  The build endpoint repeats full validation.
        return {"available":False}


def import_package(folder,api):
    apk=folder/"original.apk"
    with ZipFile(apk) as z:
        entries=z.infolist()
        if (len(entries)>60000 or len(set(z.namelist()))!=len(entries) or
            sum(i.file_size for i in entries)>1024**3 or "AndroidManifest.xml" not in z.namelist()):
            raise ValueError("不是支持的完整 APK 或解包尺寸超限")
    facts=inspect_package(apk,TOOLS)
    certs=signing_fingerprints(apk)
    facts.update(certificate_sha256=certs[0],signer_certificates_sha256=certs)
    state={"id":folder.name,"created":now(),"api":api,"package":facts,
           "embedded_native_payload":embedded_native_summary(apk),
           "assessment":assess(facts,api,[]),"status":"ready","events":[],"tests":[],"export":None}
    save(folder,state)
    return state


def editing_ready():
    from offline_kit import discover_java_home
    return bool(discover_java_home() and all((TOOLS / name).is_file()
                for name in ("aapt.exe", "zipalign.exe", "lib/apksigner.jar")))


@app.get("/api/config")
def config():
    model=public_config()
    return {"token":TOKEN,"targets":{str(a):ready(a) for a in INSTALL_FLOOR},"legacy":ready(30),
            "editing_ready":editing_ready(), "desktop_protocol":1, "demo_label":"旧版记事本" if (ROOT/"old_apk/OI-Notepad-1.3.apk").is_file() else "归档读取器",
            "model":model["model"] if model["enabled"] else None}


@app.post("/api/import/{api}")
async def ingest(api:int,request:Request,demo:bool=False):
    if api not in INSTALL_FLOOR or not (ready(api) or editing_ready()):
        raise HTTPException(400,"请选择已配置的目标系统")
    folder=STORAGE/uuid.uuid4().hex
    folder.mkdir()
    try:
        if demo:
            source=ROOT/"old_apk/OI-Notepad-1.3.apk"
            if not source.is_file():
                source=ROOT/"fixtures/archive-reader/archive-reader-broken.apk"
            shutil.copyfile(source,folder/"original.apk")
        else:
            size=0
            with (folder/"original.apk").open("xb") as f:
                async for chunk in request.stream():
                    size+=len(chunk)
                    if size>320*1024**2:
                        raise ValueError("仅接受 320 MiB 以内的单个 APK")
                    f.write(chunk)
        return import_package(folder,api)
    except Exception as exc:
        shutil.rmtree(folder)
        raise HTTPException(400,str(exc)[:1200]) from exc


@app.get("/api/jobs/{job}")
def status(job:str):
    folder=job_dir(job)
    state=read(folder)
    if state.get("export") and not state.get("versions") and operation.acquire(blocking=False):
        try:
            state=read(folder)
            archive_version(folder,state)
            save(folder,state)
        finally:
            operation.release()
    if state["status"]=="running" and state.get("worker")!=INSTANCE and operation.acquire(blocking=False):
        try:
            state=read(folder)
            if state["status"]=="running" and state.get("worker")!=INSTANCE:
                message="上次执行被工作台重启中断，可重新验证。"
                if state.get("action")=="chat-localize":
                    add_message(state,"assistant","汉化被工作台重启中断。可重新发送“汉化成中文”。")
                    state["status"]="error"
                else:
                    state.update(status="error",export=None)
                state["events"].append({"time":now(),"text":message})
                if state.get("action")=="modify":
                    change=state["modification"]
                    change.update(status="interrupted",message=message)
                    change["runs"][-1].update(status="interrupted",failure=message)
                    matrix=state["compatibility"]
                    matrix.update(status="failed",artifact=None)
                    for row in matrix["rows"]:
                        if row["status"] in {"pending","running"}:
                            row.update(status="skipped",failure=message)
                    change["runs"][-1]["compatibility"]=matrix
                save(folder,state)
        finally:
            operation.release()
    return state


@app.get("/api/jobs")
def history():
    jobs=[]
    for file in STORAGE.glob("*/state.json"):
        if not re.fullmatch(r"[0-9a-f]{32}",file.parent.name):
            continue
        try:
            s=read(file.parent)
            jobs.append({"id":s["id"],"label":s["package"]["label"],"api":s["api"],"created":s["created"],
                         "updated":s.get("updated",s["created"]),"phone":(s.get("versions") or [{}])[-1].get("phone")})
        except (OSError,KeyError,ValueError):
            continue
    return sorted(jobs,key=lambda j:j["updated"],reverse=True)


def run_chat_localization(folder, state, source, run_id, use_model):
    def progress(text):
        add_message(state, "assistant", text)
        save(folder, state)
    try:
        from auto_localize import build
        directory = folder / "localizations" / run_id
        directory.mkdir(parents=True)
        result = build(source, directory / "candidate.apk", state["api"], TOOLS, use_model, progress)
        info = result["transform"]
        state.setdefault("localized_candidates", {})[run_id] = result
        text = f"已生成签名汉化候选，已翻译 {info['translated']} 项，扫描到 {info['discovered']} 项。"
        if "fonts" in info:
            text += f" 已核对 {info['fonts']} 套游戏字库，译文写入英文语言槽。"
        add_message(state, "assistant", text + " 尚待在目标设备上试玩。", localization_id=run_id)
        state["status"] = "ready"
    except Exception as exc:
        state["status"] = "error"
        add_message(state, "assistant", "本次汉化未生成可下载的 APK。" + str(exc)[:600])
    finally:
        try:
            state["updated"] = now()
            save(folder, state)
        finally:
            operation.release()


@app.get("/api/jobs/{job}/localized/{run_id}")
def localized_candidate(job: str, run_id: str):
    folder = job_dir(job)
    state = read(folder)
    if not re.fullmatch("[a-f0-9]{32}", run_id) or run_id not in state.get("localized_candidates", {}):
        raise HTTPException(404, "汉化候选不存在")
    path = folder / "localizations" / run_id / "candidate.apk"
    if not path.is_file():
        raise HTTPException(404, "候选文件缺失")
    return FileResponse(path, filename=f"{state['package']['package']}-zh-android-api{state['api']}.apk")


@app.post("/api/jobs/{job}/message")
def chat_message(job: str, body: dict):
    from auto_translation import is_command
    if body.get("kind") != "issue" or body.get("attachment") or not is_command(body.get("text")):
        return feedback(job, body)
    folder = job_dir(job)
    if not operation.acquire(blocking=False):
        raise HTTPException(409, "请等待当前任务结束后再汉化")
    try:
        state = read(folder)
        selected = body.get("version")
        source = folder / "original.apk"
        if selected is not None:
            if type(selected) is not int or not any(v["number"] == selected for v in state.get("versions", [])):
                raise HTTPException(400, "所选版本不存在")
            source = folder / "versions" / f"{selected}.apk"
        run_id = uuid.uuid4().hex
        add_message(state, "user", body["text"])
        add_message(state, "assistant", "开始分析并生成汉化候选。")
        state.update(status="running", action="chat-localize", worker=INSTANCE, updated=now())
        save(folder,state)
        initial = read(folder)
        try:
            threading.Thread(target=run_chat_localization,
                             args=(folder,state,source,run_id,body.get("translate_with_model") is True),
                             daemon=True).start()
        except RuntimeError:
            state["status"] = "error"
            add_message(state, "assistant", "未能启动汉化任务，请重试。")
            save(folder, state)
            raise
        return initial
    except BaseException:
        operation.release()
        raise


@app.post("/api/jobs/{job}/feedback")
def feedback(job:str,body:dict):
    folder=job_dir(job)
    if not operation.acquire(blocking=False):
        raise HTTPException(409,"请等待当前验证结束再记录反馈")
    try:
        state=read(folder)
        archive_version(folder,state)
        record_feedback(folder,state,body)
        state["updated"]=now()
        save(folder,state)
        return state
    finally:
        operation.release()


@app.get("/api/jobs/{job}/versions/{number}")
def version_file(job:str,number:int):
    folder=job_dir(job)
    state=read(folder)
    if not any(v["number"]==number for v in state.get("versions",[])):
        raise HTTPException(404,"没有这个版本")
    return FileResponse(folder/"versions"/f"{number}.apk",filename=f"{state['package']['package']}-V{number}.apk")


@app.get("/api/jobs/{job}/attachments/{name}")
def feedback_image(job:str,name:str):
    folder=job_dir(job)
    if not re.fullmatch(r"[a-f0-9]{32}\.(?:png|jpg)",name) or not (folder/"attachments"/name).is_file():
        raise HTTPException(404,"没有这张截图")
    return FileResponse(folder/"attachments"/name)


@app.post("/api/jobs/{job}/target/{api}")
def change_target(job:str,api:int):
    source=job_dir(job)
    if api not in INSTALL_FLOOR or not (ready(api) or editing_ready()):
        raise HTTPException(400,"目标镜像不可用")
    folder=STORAGE/uuid.uuid4().hex
    folder.mkdir()
    try:
        shutil.copyfile(source/"original.apk",folder/"original.apk")
        return import_package(folder,api)
    except Exception as exc:
        shutil.rmtree(folder)
        raise HTTPException(400,str(exc)[:1200]) from exc


@app.post("/api/jobs/{job}/native-candidate")
def build_native_candidate(job:str):
    """Build an inspected embedded-native candidate without claiming success.

    This endpoint performs no device operation.  It is intentionally separate
    from /action/repair, whose export gate requires launch/functional evidence.
    """
    folder=job_dir(job)
    if not operation.acquire(blocking=False):
        raise HTTPException(409,"另一项设备操作或候选构建正在进行")
    try:
        state=read(folder)
        output=folder/"native-candidate.apk"
        native=state.get("native_candidate")
        if output.exists() and native and native.get("rule")=="embedded-native-payload":
            if native.get("candidate")=="native-candidate.apk" and native.get("source_sha256")==state["package"]["sha256"]:
                return {"built":False,"candidate":native,"message":"已存在未验收 native 候选，可下载到真机观察"}
            raise HTTPException(409,"native 候选状态与当前原包不一致；为避免跨规则复用请新建任务")
        if output.exists():
            raise HTTPException(409,"已有未知 native 候选文件；为避免覆盖请新建任务")
        fix=repair(folder/"original.apk",output,state["api"],TOOLS,"embedded-native-payload")
        fix.update(candidate="native-candidate.apk",source_sha256=state["package"]["sha256"],
                   acceptance="not-tested",exportable_for_observation=True)
        state["native_candidate"]=fix
        state["events"].append({"time":now(),"text":"已构建嵌入式 native payload 候选；未在设备验证，不宣称修复成功。"})
        save(folder,state)
        return {"built":True,"candidate":fix,"message":"候选仅供下载到目标设备观察，尚未验收"}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400,str(exc)[:1200]) from exc
    finally:
        operation.release()


def work(folder,action):
    state=read(folder)
    def log(message):
        state["events"].append({"time":now(),"text":message})
        save(folder,state)
    try:
        api=state["api"]
        facts=state["package"]
        serial=device.ensure(folder.name,30 if action=="legacy" else api,log,facts["native_abis"])
        game_trial=api==36 and facts["sha256"]==PACIFIC_HASH
        abis=shell(serial,"getprop","ro.product.cpu.abilist").split(",")
        if action!="legacy":
            state["assessment"]=assess(facts,api,abis)
        src=folder/"original.apk"
        modified=folder/"candidate.apk"
        def runtime_evidence():
            """Return only logs tied to this launch's package PID(s).

            The buffers were cleared immediately before a launch/action.  We
            still require a PID discovered from this package's own Android
            runtime/tombstone record or from its currently-live process before
            accepting a native failure as evidence.
            """
            crash=invoke([ADB,"-s",serial,"logcat","-b","crash","-d","-v","threadtime","-t","1200"],20)
            main=invoke([ADB,"-s",serial,"logcat","-b","main","-d","-v","threadtime","-t","1600"],20)
            corpus=(crash.stdout+crash.stderr+main.stdout+main.stderr).decode("utf-8","replace")
            pids={int(value) for value in re.findall(r"Process:\s*"+re.escape(facts["package"])+r"(?:[:][\w.]+)?,\s*PID:\s*(\d+)",corpus)}
            pids.update(int(value) for value in re.findall(r"pid:\s*(\d+),[^\n]*>>>\s*"+re.escape(facts["package"])+r"\s*<<<",corpus))
            live=invoke([ADB,"-s",serial,"shell","pidof",facts["package"]],10).stdout.decode().split()
            pids.update(int(value) for value in live if value.isdigit())
            if not pids:
                return {}
            scoped=[]
            for pid in sorted(pids):
                part=invoke([ADB,"-s",serial,"logcat","-d","-v","threadtime","--pid",str(pid),"-t","1800"],20)
                scoped.append((part.stdout+part.stderr).decode("utf-8","replace"))
            text="\n".join(scoped)
            result={"pids":sorted(pids),"evidence":"logcat-pid-scoped"}
            if "FATAL EXCEPTION" in text:
                result["java_crash"] = True
                missing_class = extract_missing_class(text)
                if missing_class:
                    result["missing_class"] = missing_class
            missing=re.search(r"(?:dlopen failed: (?:library )?['\"]?(lib[A-Za-z0-9_.+-]+\.so)['\"]?[^\n]*?(?:not found|needed)|could(?:n't| not) find (?:library )?['\"]?(lib[A-Za-z0-9_.+-]+\.so)['\"]?)",text,re.I)
            if missing:
                result["missing_library"]=next(value for value in missing.groups() if value)
            if re.search(r"(?:Fatal signal\s+(?:6|7|11)\b|SIG(?:SEGV|ABRT|BUS)\b)",text,re.I):
                result["native_crash"]=True
            return result
        def attempt(path,label):
            log(f"以系统默认策略全新安装{label}，尝试启动主界面")
            # This is the job's disposable AVD.  Clear before the launch so a
            # missing-library diagnosis cannot be borrowed from another app.
            invoke([ADB,"-s",serial,"logcat","-c"],15)
            result=device.install_run(path,facts)
            runtime=runtime_evidence()
            if runtime:
                result["runtime"]=runtime
            if runtime.get("missing_library"):
                log(f"运行时明确报告缺少 {runtime['missing_library']}；仅评估是否存在设备支持的完整 ABI 副本")
            if runtime.get("native_crash"):
                log("启动后观测到该包的 native 崩溃信号")
            result.update(file=label,time=now(),environment="redroid-arm-lab" if serial==ARM_LAB else "android-emulator")
            state["tests"].append(result)
            log(result["install"]["output"][-550:])
            launch=result.get("launch")
            if launch and launch.get("exit")==0:
                focused=launch.get("focused_package")
                if focused==facts["package"]:
                    log("启动后已观察到目标应用窗口")
                elif focused:
                    log(f"启动后当前窗口属于 {focused}，可在设备预览中查看")
                else:
                    log("尚未确认目标应用窗口，可在设备预览中查看")
            return result
        def scenario():
            name="probe-"+uuid.uuid4().hex[:10]
            value=probe(serial,facts["package"],state["task"],folder/name)
            runtime=runtime_evidence()
            if runtime.get("native_crash"):
                value["native_crash"]=True
                value["native_crash_evidence"]=runtime["evidence"]
                log("指定操作后观测到该包的 native 崩溃信号")
            value["evidence_folder"]=name
            state["tests"][-1]["functional_test"]=value
            log(value["message"])
            return value
        def verify_selected_targets(path,export):
            """Install one immutable candidate into every explicitly selected API.

            The repair above is deliberately performed once for the job's
            selected API.  This phase never rebuilds it for a lower API: every
            row proves the hash that will actually be exported.
            """
            nonlocal api, serial, abis
            matrix=state.get("compatibility")
            targets=matrix.get("targets") if isinstance(matrix,dict) else None
            if not matrix or not targets:
                return True
            with path.open("rb") as artifact_file:
                digest=hashlib.file_digest(artifact_file,"sha256").hexdigest()
            log("开始对同一候选包逐个执行所选 Android 版本的全新安装、启动与功能复测")
            for target_api in targets:
                set_row(matrix,target_api,status="running")
                save(folder,state)
                try:
                    serial=device.ensure(folder.name,target_api,log,facts["native_abis"])
                    api=target_api
                    abis=shell(serial,"getprop","ro.product.cpu.abilist").split(",")
                    result=attempt(path,f"修复包（Android {target_api-20}）")
                    result["matrix_api"]=target_api
                    launch=result.get("launch",{})
                    if (result["install"]["exit"] or launch.get("exit")!=0 or
                            "Status: ok" not in launch.get("output","") or
                            result.get("runtime",{}).get("missing_library") or
                            result.get("runtime",{}).get("native_crash") or result.get("runtime",{}).get("java_crash")):
                        raise ValueError("默认安装或启动未通过")
                    functional=None
                    verification_level="仅启动验证"
                    if state.get("task"):
                        functional=scenario()
                        if functional.get("result")!="passed" or functional.get("native_crash"):
                            raise ValueError("指定功能复测未通过")
                        verification_level="指定功能已验证"
                    elif facts["sha256"]==OI_HASH:
                        smoke_dir=folder/("matrix-smoke-"+uuid.uuid4().hex[:10])
                        exercise(str(ADB),serial,smoke_dir)
                        functional=json.loads((smoke_dir/"result.json").read_text())
                        if functional.get("installed_sha256")!=digest:
                            raise ValueError("笔记功能复测中的设备 APK 哈希不符")
                        verification_level="笔记功能已验证"
                    elif target_api==36 and facts["sha256"]==PACIFIC_HASH:
                        from game_probe import exercise_game
                        trial_dir=folder/("matrix-training-"+uuid.uuid4().hex[:10])
                        functional=exercise_game(serial,facts["package"],trial_dir,log)
                        if runtime_evidence().get("native_crash"):
                            raise ValueError("训练功能复测中目标应用发生原生崩溃")
                        verification_level="生涯训练功能已验证"
                    elif launch.get("focused_package")!=facts["package"]:
                        raise ValueError("未确认目标应用进入前台")
                    installed=shell(serial,"pm","path",facts["package"]).splitlines()
                    if len(installed)!=1 or not re.fullmatch(r"package:/data/app/[\w./=+~-]+/base\.apk",installed[0]):
                        raise ValueError("无法确认设备上的独立 APK 路径")
                    installed_hash=shell(serial,"sha256sum","'"+installed[0].removeprefix("package:")+"'",timeout=120).split()[0]
                    if installed_hash!=digest:
                        raise ValueError("设备上的 APK 哈希与候选文件不符")
                    details=shell(serial,"dumpsys","package",facts["package"])
                    chosen=re.search(r"(?m)^\s*primaryCpuAbi=(\S+)",details)
                    verified_environment={
                        "sdk":target_api,"supported_abis":abis,
                        "primary_cpu_abi":chosen.group(1) if chosen and chosen.group(1)!="null" else None,
                        "type":"redroid-arm-lab" if serial==ARM_LAB else "android-emulator"}
                    set_row(matrix,target_api,status="passed",candidate=result,functional=functional,
                            verification_level=verification_level,installed_sha256=installed_hash,
                            verified_environment=verified_environment)
                    log(f"Android {target_api-20} 已以相同哈希完成复测")
                except Exception as exc:
                    message=str(exc)[:1200]
                    unavailable="缺少 API" in message and "模拟器镜像" in message
                    set_row(matrix,target_api,status="unavailable" if unavailable else "failed",failure=message)
                    log(f"Android {target_api-20} 复测未通过：{message}")
                finally:
                    save(folder,state)
            passed=finish_matrix(matrix,export)
            if passed:
                # This is redundant with compatibility.targets by design: it
                # keeps downloaded reports intelligible to older clients.
                export["verified_targets"]=targets.copy()
                log("所有选定 Android 版本均已验证，可导出同一候选包")
            else:
                state["export"]=None
                log("存在未通过或不可用的目标环境；不导出候选包")
            save(folder,state)
            return passed
        if action=="candidate":
            export = state.get("export") or {}
            if export.get("modification_id"):
                from modification_runner import digest
                version = next((v for v in state.get("versions", []) if v["sha256"] == export["sha256"]), None)
                if not version:
                    raise ValueError("找不到已验证的改造版本")
                modified = folder / "versions" / f"{version['number']}.apk"
                if digest(modified) != export["sha256"]:
                    raise ValueError("归档 APK 哈希与验收记录不符")
            if not modified.exists():
                raise ValueError("此任务还没有候选包")
            attempt(modified,"修复包")
        elif action in {"original","legacy"}:
            attempt(src,"原包")
        elif action=="repair":
            state["export"]=None
            original=attempt(src,"原包")
            assessment=state["assessment"]
            failed=original["install"]["exit"]!=0
            floor="INSTALL_FAILED_DEPRECATED_SDK_VERSION" in original["install"]["output"]
            native_plan=abi_pruning_plan(facts,abis,original.get("runtime",{}).get("missing_library"))
            embedded_available=bool(state.get("embedded_native_payload",{}).get("available"))
            functional=None
            libraries=""
            if not failed and state.get("task") and not original.get("runtime",{}).get("java_crash"):
                functional=scenario()
            missing_class=(functional or {}).get("missing_class") or original.get("runtime",{}).get("missing_class")
            if missing_class:
                libraries=shell(serial,"pm","list","libraries")
            library=resolve_platform_library(missing_class,libraries)
            library_action="try-platform-http-library" if library=="org.apache.http.legacy" else "try-platform-shared-library"
            abi_install_failure="INSTALL_FAILED_NO_MATCHING_ABIS" in original["install"]["output"]
            embedded_needed=embedded_available and (bool(original.get("runtime",{}).get("native_crash")) or
                                                     bool(original.get("runtime",{}).get("missing_library")) or
                                                     bool(functional and functional.get("native_crash")) or
                                                     (failed and (floor or abi_install_failure)))
            isolation_native_experiment=embedded_available and abi_install_failure
            options=[]
            if embedded_needed:
                options.append("try-embedded-native-payload")
            if library and not failed:
                options.append(library_action)
            if native_plan and not failed:
                options.append("try-abi-pruning")
            if floor and assessment["suggested_action"]=="try-installation-floor-repair":
                options.append("try-installation-floor-repair")
            if (not options and not failed and not original.get("runtime",{}).get("java_crash")
                    and (not functional or functional["result"]=="passed")):
                options.append("test-original")
            allowed=options+["report-limit"]
            log("根据安装结果、清单与设备架构判断下一步")
            # Transmit only curated evidence. Exclude filenames, paths, code and raw log content.
            err=re.search(r"Failure \[([A-Z0-9_]+)",original["install"]["output"])
            evidence={"E1_target_sdk":facts["target_sdk"],"E2_device_api":api,"E3_native_abis":facts["native_abis"],
                      "E4_device_abis":abis,"E5_install":err.group(1) if err else ("FAILED" if failed else "SUCCESS")}
            if original.get("runtime",{}).get("missing_library"):
                evidence["E7_missing_native_library"]=original["runtime"]["missing_library"]
                evidence["E8_native_runtime_evidence"]=original["runtime"]["evidence"]
                evidence["E9_abi_pruning_plan"]=native_plan or "no-supported-complete-abi"
            if embedded_available:
                evidence["E10_embedded_native_payload"]={"available":True,"replacement_count":state["embedded_native_payload"]["replacement_count"]}
                evidence["E11_embedded_payload_needed"]=embedded_needed
                if isolation_native_experiment:
                    evidence["E12_isolation_native_experiment"]="INSTALL_FAILED_NO_MATCHING_ABIS"
            if original.get("runtime",{}).get("native_crash") or (functional and functional.get("native_crash")):
                evidence["E13_native_crash_evidence"]="logcat-pid-scoped"
            if functional:
                evidence["E6_functional_test"]=functional["result"]
                if functional.get("exception"):
                    evidence["E9_exception"]=functional["exception"]
            if missing_class:
                evidence.update(E7_missing_class=missing_class,E8_platform_library=library or "absent")
            def inspect_structure():
                with ZipFile(src) as archive:
                    entries=archive.namelist()
                return {"min_sdk":facts["min_sdk"], "target_sdk":facts["target_sdk"],
                        "native_abis":facts["native_abis"], "has_native_code":bool(facts["native_abis"]),
                        "entry_counts":{"dex":sum(bool(re.fullmatch(r"classes(?:[0-9]+)?\.dex", n)) for n in entries),
                                        "total":len(entries)}}
            def diagnose_plan(observed, actions):
                missing = observed.get("E7_missing_class")
                def lookup():
                    listing = shell(serial,"dumpsys","package","libraries")
                    def read_jar(path):
                        # Only immutable system paths from the local catalog reach this adapter.
                        size = shell(serial,"stat","-c","%s",path).strip()
                        if not size.isdecimal() or not 0 < int(size) <= MAX_LIBRARY_JAR_BYTES:
                            raise ValueError("设备共享库大小超出检查范围")
                        result = invoke([ADB,"-s",serial,"exec-out","cat",path],20)
                        if result.returncode or len(result.stdout) != int(size):
                            raise ValueError("共享库读取不完整")
                        return result.stdout
                    return discover_shared_library(missing,listing,read_jar)
                reply = advise(observed,actions,actions[0],inspect_structure,
                               lookup_library=lookup if missing and observed.get("E8_platform_library")=="absent" else None)
                if reply.get("verified_library"):
                    observed["E8_platform_library"] = reply["verified_library"]
                return reply
            advice=diagnose_plan(evidence,allowed)
            library=advice.get("verified_library",library)
            state["diagnosis"]={"evidence":evidence,"advice":advice}
            log(advice["reason"])
            if advice["action"]=="report-limit":
                for finding in assessment.get("static_findings",[]):
                    if not finding.get("remediable"):
                        log("兼容性限制："+finding["evidence"])
                log("本次证据不足以生成可验证修复包，请查看诊断报告")
            else:
                path=src
                result=original
                if advice["action"] in ("try-installation-floor-repair","try-platform-http-library","try-platform-shared-library","try-abi-pruning","try-embedded-native-payload"):
                    rule="installation-floor" if advice["action"]=="try-installation-floor-repair" else "platform-http-library"
                    remove_abis=()
                    if advice["action"]=="try-abi-pruning":
                        if not native_plan:
                            raise ValueError("缺少库证据未找到设备支持的完整替代 ABI，拒绝删除 ABI 目录")
                        rule="drop-incomplete-abi"
                        remove_abis=tuple(native_plan["remove_abis"])
                    elif advice["action"]=="try-embedded-native-payload":
                        rule="embedded-native-payload"
                    elif advice["action"]=="try-platform-shared-library":
                        rule="platform-shared-library"
                    selected_library=library if rule in ("platform-http-library","platform-shared-library") else None
                    expected_removed=list(remove_abis)
                    current=state.get("patch",{})
                    same_candidate=(current.get("rule")==rule and current.get("source_sha256")==facts["sha256"] and
                                    (rule!="embedded-native-payload" or current.get("native_recipe_version")==2) and
                                    current.get("removed_abis",[])==expected_removed and
                                    (not selected_library or current.get("added_library")==selected_library))
                    if modified.exists() and not same_candidate:
                        retained=state.get("native_candidate",{})
                        if retained.get("candidate")=="candidate.apk":
                            raise ValueError("现有 candidate.apk 是保留的隔离实验候选；请新建任务避免跨规则覆盖")
                        modified.unlink()
                    if not modified.exists():
                        if rule=="installation-floor":
                            log("调整已定位的安装门槛，重新对齐、签名并验签")
                        elif selected_library:
                            log("声明已验证存在的 Android 共享库，重新对齐、签名并验签")
                        elif rule=="embedded-native-payload":
                            log("已识别包内同架构原生实现；检查引擎版本是否有经核对的位置无关代码补丁")
                        else:
                            log("只剔除缺少已证实核心库的 ABI 目录，重新对齐、签名并验签")
                        fix=repair(src,modified,api,TOOLS,rule,remove_abis,
                                   shared_library=selected_library, verified_shared_libraries=(selected_library,) if advice.get("verified_library") else (),
                                   modernize_crt=(rule=="embedded-native-payload" and facts["target_sdk"]<INSTALL_FLOOR[api]))
                        if fix.get("pic_libraries"):
                            log(f"已改写 {len(fix['pic_libraries'])} 个库的已知重定位并将目标 API 调整为 {fix['after']}")
                        if rule=="embedded-native-payload":
                            fix["native_recipe_version"]=2
                        fix.update(candidate="candidate.apk",source_sha256=facts["sha256"],
                                   isolation_experiment=isolation_native_experiment if rule=="embedded-native-payload" else False)
                        state["patch"]=fix
                    result=attempt(modified,"修复包")
                    path=modified
                    # A target-SDK repair can be necessary before the linker
                    # failure becomes observable.  Only then may it be combined
                    # with ABI pruning, and only from the untouched original.
                    chained=abi_pruning_plan(facts,abis,result.get("runtime",{}).get("missing_library"))
                    if rule not in ("drop-incomplete-abi","embedded-native-payload") and chained:
                        log("修复包运行时仍有精确缺库证据；生成包含相同清单修复的 ABI 剔除候选")
                        if modified.exists():
                            modified.unlink()
                        rules=(rule,"drop-incomplete-abi")
                        fix=repair(src,modified,api,TOOLS,rules,tuple(chained["remove_abis"]),shared_library=selected_library,
                                   verified_shared_libraries=(selected_library,) if advice.get("verified_library") else ())
                        fix["candidate"]="candidate.apk"
                        state["patch"]=fix
                        result=attempt(modified,"组合修复包")
                        path=modified
                # A targetSdk install failure can mask a second, unrelated class-loading error.
                # Rebuild from the input and keep the first candidate intact until signing succeeds.
                verified_functional=None
                if path==modified and state["patch"].get("rule")=="installation-floor" and not result["install"]["exit"]:
                    next_failure=result.get("runtime",{})
                    if state.get("task") and not next_failure.get("java_crash"):
                        verified_functional=scenario()
                        next_failure=verified_functional
                    second_class=next_failure.get("missing_class")
                    if second_class:
                        known=resolve_platform_library(second_class,shell(serial,"pm","list","libraries"))
                        observed={"E2_device_api":api,"E5_install":"SUCCESS","E7_missing_class":second_class,
                                  "E8_platform_library":known or "absent","E10_applied_rule":"installation-floor"}
                        actions=["report-limit"]
                        if known:
                            actions.insert(0,"try-platform-http-library" if known=="org.apache.http.legacy" else "try-platform-shared-library")
                        followup=diagnose_plan(observed,actions)
                        state["candidate_diagnosis"]={"evidence":observed,"advice":followup}
                        log(followup["reason"])
                        if followup["action"]=="report-limit":
                            raise ValueError("安装门槛修复后仍有未解决的缺类故障")
                        lib=followup.get("verified_library",known)
                        staged=folder/("combined-"+uuid.uuid4().hex+".apk")
                        try:
                            fix=repair(src,staged,api,TOOLS,("installation-floor","platform-shared-library"),
                                       shared_library=lib,verified_shared_libraries=(lib,))
                            staged.replace(modified)
                        finally:
                            staged.unlink(missing_ok=True)
                        state["patch"]={**fix,"source_sha256":facts["sha256"],"candidate":"candidate.apk"}
                        verified_functional=None
                        result=attempt(modified,"安装门槛与共享库组合修复包")
                launch=result.get("launch",{})
                if (result["install"]["exit"] or launch.get("exit")!=0 or
                        "Status: ok" not in launch.get("output","") or
                        result.get("runtime",{}).get("missing_library") or
                        result.get("runtime",{}).get("native_crash") or result.get("runtime",{}).get("java_crash") or
                        (path==modified and isolation_native_experiment)):
                    log("未通过安装或启动验证")
                    if state.get("patch",{}).get("rule")=="embedded-native-payload":
                        state["native_candidate"]={**state["patch"],"candidate":"candidate.apk",
                                                   "acceptance":"failed","exportable_for_observation":True}
                        for finding in assessment.get("static_findings",[]):
                            log("兼容性限制："+finding["evidence"])
                        log("保留该 native 候选供观察；未通过不作为已修复 APK 导出")
                elif not game_trial and not state.get("task") and facts["sha256"]!=OI_HASH and launch.get("focused_package")!=facts["package"]:
                    log("尚未确认目标应用进入前台，本次未导出 APK")
                else:
                    level="启动已验证，功能待核验"
                    if state.get("task"):
                        functional=(verified_functional or scenario()) if path==modified else functional
                        if not functional or functional["result"]!="passed" or functional.get("native_crash"):
                            if path==modified and functional:
                                observed={"E2_device_api":api,"E5_install":"SUCCESS","E6_functional_test":functional["result"],
                                          "E10_applied_rule":state["patch"]["rule"]}
                                if functional.get("exception"):
                                    observed["E9_exception"]=functional["exception"]
                                verdict=advise(observed,["report-limit"],"report-limit")
                                state["candidate_diagnosis"]={"evidence":observed,"advice":verdict}
                                log(verdict["reason"])
                            raise ValueError("功能复测未通过；请查看报告")
                        level="指定操作已验证"
                        state["acceptance"]=functional
                    elif facts["sha256"]==OI_HASH:
                        log("验证笔记新建、保存、冷启动和正文一致性")
                        out=folder/("smoke-"+uuid.uuid4().hex[:10])
                        exercise(str(ADB),serial,out)
                        smoke=json.loads((out/"result.json").read_text())
                        if smoke["installed_sha256"]!=hashlib.sha256(path.read_bytes()).hexdigest():
                            raise ValueError("设备上的 APK 哈希与导出文件不符")
                        state["acceptance"]=smoke
                        level="笔记功能已验证"
                    if game_trial:
                        from game_probe import exercise_game
                        trial_dir=folder/("training-"+uuid.uuid4().hex[:10])
                        try:
                            trial=exercise_game(serial,facts["package"],trial_dir,log)
                            if runtime_evidence().get("native_crash"):
                                raise ValueError("训练过程中目标应用发生原生崩溃")
                        except Exception as exc:
                            state["tests"][-1]["training"]={"result":"failed","message":str(exc)[:600],
                                                           "evidence_folder":trial_dir.name}
                            if path==modified and state["patch"]["rule"]=="embedded-native-payload":
                                state["native_candidate"]={**state["patch"],"acceptance":"failed",
                                                           "exportable_for_observation":True}
                            raise
                        state["tests"][-1]["training"]=trial
                        state["training"]=trial
                        level="生涯训练中已验证格挡及闪避提示"
                    installed=shell(serial,"pm","path",facts["package"]).splitlines()
                    if len(installed)!=1 or not re.fullmatch(r"package:/data/app/[\w./=+~-]+/base\.apk",installed[0]):
                        raise ValueError("无法确认设备上的独立 APK 路径")
                    # Quote the path for adb's remote shell, including on older Android releases.
                    installed_hash=shell(serial,"sha256sum","'"+installed[0].removeprefix("package:")+"'",
                                         timeout=120).split()[0]
                    with path.open("rb") as apk:
                        digest=hashlib.file_digest(apk,"sha256").hexdigest()
                    generated_hash=state["patch"]["sha256"] if path==modified else facts["sha256"]
                    if digest!=generated_hash:
                        raise ValueError("待导出 APK 与本次构建或导入的哈希不符")
                    if installed_hash!=digest:
                        raise ValueError("设备上的 APK 哈希与导出文件不符")
                    details=shell(serial,"dumpsys","package",facts["package"])
                    chosen=re.search(r"(?m)^\s*primaryCpuAbi=(\S+)",details)
                    proposal={"file":path.name,"level":level,"sha256":digest,
                                     "installed_sha256":installed_hash,"verified_environment":{
                                         "sdk":result["api"],"supported_abis":abis,
                                         "primary_cpu_abi":chosen.group(1) if chosen and chosen.group(1)!="null" else None,
                                         "type":"redroid-arm-lab" if serial==ARM_LAB else "android-emulator"},
                                     "signer_certificates_sha256":signing_fingerprints(path,api)}
                    if not verify_selected_targets(path,proposal):
                        raise ValueError("所选 Android 版本未全部通过复测，候选包未导出")
                    state["export"]=proposal
                    if state.get("native_candidate",{}).get("sha256")==digest:
                        state.pop("native_candidate",None)
                    log(level)
        matrix=state.get("compatibility")
        if action=="repair" and matrix and matrix["status"]=="running" and not state.get("export"):
            raise ValueError("主目标未产生可复测的 APK，无法确认所选范围")
        state["status"]="ready"
    except Exception as exc:
        state["status"]="error"
        matrix=state.get("compatibility")
        if isinstance(matrix,dict) and matrix.get("status")=="running":
            # A primary build/verification failure means no immutable artifact
            # existed to test on the remaining environments.  Preserve that
            # fact as a failed matrix rather than leaving a misleading spinner.
            finish_matrix(matrix,None)
            for row in matrix["rows"]:
                if row["status"] in ("pending","running"):
                    if row["api"]==state["api"]:
                        set_row(matrix,row["api"],status="failed",failure=str(exc)[:600])
                    else:
                        set_row(matrix,row["api"],status="skipped",failure="主目标未产生可验证的文件")
        log(str(exc)[:1200])
    finally:
        try:
            if action=="repair":
                previous=len(state.get("versions",[]))
                if state.get("export"):
                    try:
                        version=archive_version(folder,state)
                        if previous==len(state["versions"]):
                            add_message(state,"assistant",f"复测使用的文件与 V{version['number']} 相同，已保留原版本。")
                    except (ValueError,OSError) as exc:
                        state.update(status="error",export=None)
                        add_message(state,"assistant",str(exc))
                else:
                    msg=state["events"][-1]["text"] if state["events"] else "未通过验证"
                    add_message(state,"assistant",f"本轮未产生已验证的新版本。{msg}")
            save(folder,state)
        finally:
            operation.release()


@app.post("/api/jobs/{job}/modify/resources")
def modification_resources(job: str, body: dict):
    from apk_edit_build import inspect_decoded
    from localization_edit import catalog
    from modification_runner import digest
    folder = job_dir(job)
    if not operation.acquire(blocking=False):
        raise HTTPException(409, "请等待当前任务结束")
    try:
        state = read(folder)
        base = body.get("base_version")
        source = folder / "original.apk"
        expected = state["package"]["sha256"]
        if base is not None:
            if type(base) is not int:
                raise ValueError("基准版本无效")
            version = next((v for v in state.get("versions", []) if v["number"] == base), None)
            if version is None:
                raise ValueError("找不到选定版本")
            source = folder / "versions" / f"{base}.apk"
            expected = version["sha256"]
        if digest(source) != expected:
            raise ValueError("基准 APK 哈希不符")
        report = inspect_decoded(source, TOOLS, lambda decoded: {"strings": catalog(decoded)})
        return {**report["inspection"], "source_sha256": expected}
    except (ValueError, RuntimeError, OSError) as exc:
        raise HTTPException(422, str(exc)[:1200]) from exc
    finally:
        operation.release()


@app.post("/api/jobs/{job}/modify/plan")
def plan_modification(job:str,body:dict):
    from modification_tasks import make_plan
    folder=job_dir(job)
    if not operation.acquire(blocking=False):
        raise HTTPException(409,"请等待当前任务结束")
    try:
        state=read(folder)
        plan=make_plan(state,body)
        previous=state.get("modification",{})
        state["modification"]={"plan":plan,"status":"planned","runs":previous.get("runs",[]),
                               "message":"已保存验收条件，执行时将先验证基准 APK 的实际行为。"}
        add_message(state,"user",plan["request"],modification_id=plan["id"])
        state["updated"]=now()
        save(folder,state)
        return {"plan":plan}
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc
    finally:
        operation.release()


@app.post("/api/jobs/{job}/modify/run")
def run_modification(job:str,body:dict):
    from modification_runner import run_worker, start_run
    folder=job_dir(job)
    if not operation.acquire(blocking=False):
        raise HTTPException(409,"已有设备操作正在执行")
    try:
        state=read(folder)
        archive_version(folder,state)
        start_run(state,body.get("plan_id"))
        state["worker"]=INSTANCE
        save(folder,state)
        threading.Thread(target=run_worker,args=(folder,state,device,save,signing_fingerprints,now,operation),
                         daemon=True).start()
        return {"started":True}
    except ValueError as exc:
        operation.release()
        raise HTTPException(409,str(exc)) from exc
    except Exception:
        operation.release()
        raise


@app.post("/api/jobs/{job}/action/{action}")
def begin(job:str,action:str,body:dict|None=None):
    folder=job_dir(job)
    if action not in {"original","candidate","legacy","repair","retry"}:
        raise HTTPException(400,"未知操作")
    if not operation.acquire(blocking=False):
        raise HTTPException(409,"另一项设备操作正在进行")
    try:
        body=dict(body or {})
        state=read(folder)
        state.pop("attempt_feedback",None)
        if action=="retry":
            issue=state.get("pending_feedback")
            if not issue:
                raise HTTPException(400,"请先反馈要继续修复的问题")
            action="repair"
            state["attempt_feedback"]=issue["id"]
            if not body.get("task"):
                body["task"]=issue.get("task")
            add_message(state,"assistant","开始按本次反馈重新诊断和验证。",feedback_id=issue["id"])
        if action=="repair":
            archive_version(folder,state)
            task=body.get("task")
            if task is not None and (not isinstance(task,dict) or
                    not all(isinstance(task.get(k),str) and 0<len(task[k])<=120 for k in ("tap","expect"))):
                raise HTTPException(400,"请同时填写控件文案与预期内容")
            state["task"]=task
            state["export"]=None
            if "targets" in body:
                try:
                    targets=normalize_targets(body["targets"],INSTALL_FLOOR,state["api"])
                except ValueError as exc:
                    raise HTTPException(422,str(exc)) from exc
                if max(targets)!=state["api"]:
                    raise HTTPException(422,"多目标验证的最高 Android API 必须与当前任务目标一致；请先切换目标版本")
                state["compatibility"]=new_matrix(targets,state["api"])
            else:
                # Absence, unlike an empty array, means legacy one-target
                # behavior and keeps existing API clients compatible.
                state.pop("compatibility",None)
            for key in ("training","acceptance","candidate_diagnosis"):
                state.pop(key,None)
        state.update(status="running",action=action,worker=INSTANCE)
        save(folder,state)
        threading.Thread(target=work,args=(folder,action),daemon=True).start()
        return {"started":True,"targets":state.get("compatibility",{}).get("targets",[state["api"]])}
    except Exception:
        operation.release()
        raise


@app.get("/api/jobs/{job}/screen")
def screen(job:str):
    job_dir(job)
    try:
        device.check(job)
        p=invoke([ADB,"-s",device.serial,"exec-out","screencap","-p"],10)
        if not p.stdout.startswith(b"\x89PNG"):
            raise ValueError("尚未获得屏幕")
        return Response(p.stdout,media_type="image/png")
    except Exception as exc:
        raise HTTPException(409,str(exc))


@app.post("/api/jobs/{job}/input")
def interact(job:str,body:dict):
    job_dir(job)
    if not operation.acquire(blocking=False):
        raise HTTPException(409,"自动验证期间暂时锁定设备操作")
    try:
        device.check(job)
        event=body.get("event")
        if event=="tap":
            x,y=int(body["x"]),int(body["y"])
            if not (0<=x<720 and 0<=y<1280):
                raise ValueError("无效坐标")
            shell(device.serial,"input","tap",str(x),str(y))
        elif event=="text" and re.fullmatch(r"[A-Za-z0-9_ .-]{1,80}",body.get("value","")):
            shell(device.serial,"input","text",body["value"].replace(" ","%s"))
        elif event=="back":
            shell(device.serial,"input","keyevent","4")
        else:
            raise ValueError("无效输入")
        return {"ok":True}
    except Exception as exc:
        raise HTTPException(400,str(exc))
    finally:
        operation.release()


@app.get("/api/jobs/{job}/export/{kind}")
def download(job:str,kind:str):
    folder=job_dir(job)
    state=read(folder)
    if kind=="report":
        return FileResponse(folder/"state.json",filename=f"reviver-{job[:8]}.json")
    if kind=="native-candidate":
        native=state.get("native_candidate")
        filename=native.get("candidate") if isinstance(native,dict) else None
        path=folder/filename if filename in {"native-candidate.apk","candidate.apk"} else None
        if not native or native.get("acceptance") not in {"not-tested","failed"} or not path or not path.is_file():
            raise HTTPException(409,"尚未生成可供观察的 native 候选")
        return FileResponse(path,filename=f"{state['package']['package']}-native-candidate.apk")
    if kind!="apk" or not state.get("export"):
        raise HTTPException(409,"尚未验证可导出的安装包")
    return FileResponse(folder/state["export"]["file"],filename=f"{state['package']['package']}-api{state['api']}.apk")


from app_additions import install_routes
from updates_router import router as updates_router
app.include_router(updates_router)
install_routes(app,lambda job: read(job_dir(job)),operation)

app.mount("/",StaticFiles(directory=ROOT/"web",html=True),name="web")
