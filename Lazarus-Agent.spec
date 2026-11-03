# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
root = Path(SPECPATH)
a = Analysis([str(root/"desktop.py")], pathex=[str(root)],
    datas=[(str(root/"web"),"web"),(str(root/"tools/start_lab.ps1"),"tools"),
           (str(root/"fixtures/archive-reader/archive-reader-broken.apk"),"fixtures/archive-reader")],
    hiddenimports=["uvicorn.logging","uvicorn.loops.asyncio","uvicorn.protocols.http.h11_impl",
                   "uvicorn.lifespan.on","uvicorn.lifespan.off","game_probe","tools.serve_arm_lab"],
    excludes=["matplotlib","pytest","tkinter","IPython"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz,a.scripts,a.binaries,a.datas,[],name="Lazarus-Agent",console=False,upx=False)
