"""Speaks important events through NVDA, when NVDA is running.

Uses NV Access's nvdaControllerClient64.dll (LGPL 2.1, part of NVDA's controller client), which sits
beside the exe. Without NVDA, or without the DLL, speaking does nothing.
"""

from __future__ import annotations

import ctypes
import os

from . import paths

_dll = None
_tried = False


def _load():
    global _dll, _tried
    if not _tried:
        _tried = True
        path = os.path.join(paths.APP, "nvdaControllerClient64.dll")
        if os.path.exists(path):
            try:
                _dll = ctypes.windll.LoadLibrary(path)
            except OSError:
                _dll = None
    return _dll


def speak(text: str, interrupt: bool = False) -> bool:
    dll = _load()
    if not dll:
        return False
    try:
        if dll.nvdaController_testIfRunning() != 0:
            return False
        if interrupt:
            dll.nvdaController_cancelSpeech()
        return dll.nvdaController_speakText(ctypes.c_wchar_p(text)) == 0
    except OSError:
        return False
