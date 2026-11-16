"""Make the bundled ONNX Runtime provider directory visible to Windows DLL lookup."""

import os
import sys


if sys.platform == "win32" and hasattr(os, "add_dll_directory"):
    _roots = [getattr(sys, "_MEIPASS", ""), os.path.dirname(sys.executable)]
    _candidates = []
    for _root in _roots:
        if _root:
            _candidates.extend((os.path.join(_root, "onnxruntime", "capi"),
                                os.path.join(_root, "_internal", "onnxruntime", "capi")))
    for _capi in dict.fromkeys(_candidates):
        if os.path.isdir(_capi):
            os.add_dll_directory(_capi)
