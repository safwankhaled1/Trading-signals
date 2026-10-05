"""Update external QML and reopen only the desktop, leaving trading workers running."""
from pathlib import Path
import ctypes
import os
import shutil
import subprocess
import sys

import psutil

project = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project))
from tools.apply_update import within_project


def main():
    source = within_project(project / "signaldesk/qml/Main.qml")
    release = within_project(project / "dist/GoldSignalDesk-0.1.1")
    target = within_project(release / "_internal/signaldesk/qml/Main.qml")
    staged = within_project(project / ".tools/update-package/GoldSignalDesk-0.1.1/_internal/signaldesk/qml/Main.qml")
    executable = release / "GoldSignalDesk.exe"
    if not all(p.is_file() for p in (source, target, executable)):
        raise RuntimeError("Existing release or QML file is missing")
    processes = [p for p in psutil.process_iter(["pid", "exe", "cmdline"])
                 if p.info["exe"] and Path(p.info["exe"]).resolve() == executable]
    workers = [p for p in processes if "--engine" in (p.info["cmdline"] or [])]
    desktops = [p for p in processes if p not in workers]
    backup = within_project(project / ".tools/ui-backup/Main-before-decimal.qml")
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, backup)
    for destination in [target] + ([staged] if staged.is_file() else []):
        temporary = destination.with_suffix(".qml.tmp")
        temporary.write_bytes(source.read_bytes())
        os.replace(temporary, destination)

    desktop_ids = {p.pid for p in desktops}
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
    user32.EnumWindows.argtypes = [callback_type, ctypes.c_void_p]
    def close_window(hwnd, _):
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in desktop_ids:
            user32.PostMessageW(hwnd, 0x0010, 0, 0)
        return True
    callback = callback_type(close_window)
    user32.EnumWindows(callback, 0)
    for desktop in desktops:
        desktop.wait(timeout=8)
    if any(not worker.is_running() for worker in workers):
        raise RuntimeError("A trading worker exited during the desktop-only update")
    subprocess.Popen([str(executable), "--mode", "live", "--page", "5"], cwd=str(project),
                     creationflags=subprocess.DETACHED_PROCESS,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("Updated the existing 0.1.1 QML and reopened settings; existing trading workers were not stopped.")


if __name__ == "__main__":
    main()
