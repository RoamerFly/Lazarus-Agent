"""Read-only question and settings endpoints, mounted before the UI catch-all."""
import os
from fastapi import HTTPException

from assistant_chat import answer_question
from storage_paths import resolve_storage_paths, jobs_dir
import bench_device


def install_routes(app, load_job):
    def locations():
        storage=resolve_storage_paths()
        return {"root":("存储根",storage.root),"jobs":("任务、证据和各版本 APK",jobs_dir()),
                "sdk":("Android SDK",bench_device.SDK),"lab":("模拟器工作区",bench_device.LAB)}

    @app.get("/api/resources")
    def resources():
        tools={"ADB":bench_device.ADB,"AAPT":bench_device.TOOLS/"aapt.exe",
               "ZIP 对齐":bench_device.TOOLS/"zipalign.exe",
               "APK 签名":bench_device.TOOLS/"lib/apksigner.jar",
               "Android 模拟器":bench_device.SDK/"emulator/emulator.exe"}
        return {"locations":[{"id":key,"label":label,"path":str(path),"available":path.is_dir()}
                             for key,(label,path) in locations().items()],
                "tools":[{"name":name,"path":str(path),"exists":path.is_file()} for name,path in tools.items()]}

    @app.post("/api/resources/open/{kind}")
    def open_resource(kind:str):
        choice=locations().get(kind)
        if not choice or not choice[1].is_dir() or os.name!="nt":
            raise HTTPException(400,"该目录尚不可用")
        os.startfile(str(choice[1]))
        return {"opened":True}

    def explain(state, body):
        use_model = body.get("use_model", False)
        if not isinstance(use_model, bool):
            raise HTTPException(400, "模型开关必须是布尔值")
        try:
            return {"answer": answer_question(state, body.get("question", ""), use_model)}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/ask")
    def ask_general(body: dict):
        return explain({}, body)

    @app.post("/api/jobs/{job}/ask")
    def ask_job(job: str, body: dict):
        return explain(load_job(job), body)
