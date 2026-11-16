"""Make the bundled ONNX Runtime provider directory visible to Windows DLL lookup."""

import os
import sys


if sys.platform == "win32" and hasattr(os, "add_dll_directory"):
    _capi = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)), "onnxruntime", "capi")
    if os.path.isdir(_capi):
        os.add_dll_directory(_capi)
