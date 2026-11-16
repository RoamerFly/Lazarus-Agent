# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules
root = Path(SPECPATH)
onnx_capi = root.parent / "Python" / "Python310" / "lib" / "site-packages" / "onnxruntime" / "capi"
if not onnx_capi.is_dir():
    import onnxruntime
    onnx_capi = Path(onnxruntime.__file__).parent / "capi"
onnx_binaries = [(str(path), "onnxruntime/capi") for path in onnx_capi.glob("*.dll")]
onnx_binaries += [(str(path), "onnxruntime/capi") for path in onnx_capi.glob("*.pyd")]
onnx_binaries += [(str(path), ".") for path in onnx_capi.glob("*.dll")]
a = Analysis([str(root/"desktop.py")], pathex=[str(root)],
    binaries=collect_dynamic_libs("ctranslate2") + onnx_binaries,
    runtime_hooks=[str(root/"pyi_rth_onnxruntime.py")],
    datas=collect_data_files("rapidocr_onnxruntime")+collect_data_files("opencc")+collect_data_files("UnityPy")+[(str(root/"web"),"web"),(str(root/"tools/start_lab.ps1"),"tools"),
           (str(root/"update/lazarus-update-public.pem"),"update"),
           (str(root/"fixtures/archive-reader/archive-reader-broken.apk"),"fixtures/archive-reader")],
    hiddenimports=collect_submodules("rapidocr_onnxruntime")+["uvicorn.logging","uvicorn.loops.asyncio","uvicorn.protocols.http.h11_impl",
                   "uvicorn.lifespan.on","uvicorn.lifespan.off","game_probe","tools.serve_arm_lab",
                   "cryptography.hazmat.backends.openssl","cryptography.hazmat.bindings._rust",
                   "apply_update","updater","updates_router","modification_tasks","modification_runner",
                   "remember_file","apk_edit_build","modification_probe","localization_probe","localization_edit"],
    excludes=["matplotlib","pytest","tkinter","IPython","torch","tensorflow","transformers","ctranslate2.converters","ctranslate2.specs"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz,a.scripts,a.binaries,a.datas,[],name="Lazarus-Agent",icon=str(root/"web/assets/lazarus.ico"),console=False,upx=False)
