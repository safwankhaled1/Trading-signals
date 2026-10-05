"""Replace the existing portable installation after verifying idle worker state."""
from __future__ import annotations

import argparse
import ctypes
import shutil
import subprocess
import sys
import os
from multiprocessing.connection import Client
from pathlib import Path

import psutil

project = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project))
from signaldesk.config import data_directory
from signaldesk.security import load_secret


def within_project(path):
    resolved = path.resolve()
    if not resolved.is_relative_to(project.resolve()) or resolved == project.resolve():
        raise RuntimeError("Update path is outside the project")
    return resolved


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--restart", action="store_true")
    parser.add_argument("--engine-stopped", action="store_true")
    args = parser.parse_args()
    source = within_project(project / ".tools" / "update-package" / "GoldSignalDesk-0.1.1")
    target = within_project(project / "dist" / "GoldSignalDesk-0.1.1")
    backup = within_project(project / ".tools" / "update-backup-0.1.1")
    executable = target / "GoldSignalDesk.exe"
    if not (source / executable.name).is_file() or not executable.is_file():
        raise RuntimeError("The staged application or existing installation is missing")
    preview = project / ".video-review" / "staged-update-preview.png"
    if preview.exists():
        preview.unlink()
    check = subprocess.Popen([str(source / executable.name), "--preview", str(preview), "--page", "6"],
                             env={**os.environ, "QT_QPA_PLATFORM": "offscreen", "QT_QUICK_BACKEND": "software"},
                             creationflags=subprocess.CREATE_NO_WINDOW,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if check.wait(timeout=20) != 0 or not preview.is_file():
            raise RuntimeError("The staged desktop UI failed to start")
    finally:
        if check.poll() is None:
            check.kill()
            check.wait(timeout=5)
    processes = [p for p in psutil.process_iter(["pid", "exe", "cmdline"])
                 if p.info["exe"] and Path(p.info["exe"]).resolve() == executable]
    workers = [p for p in processes if "--engine" in (p.info["cmdline"] or [])]
    desktops = [p for p in processes if p not in workers]
    connections = []
    try:
        for worker in workers:
            cmdline = worker.info["cmdline"]
            mode = cmdline[cmdline.index("--mode") + 1] if "--mode" in cmdline else "demo"
            runtime = load_secret(data_directory() / f"{mode}.runtime")
            if runtime.get("pid") != worker.pid:
                raise RuntimeError("The worker identity differs from the runtime record")
            connection = Client(("127.0.0.1", runtime["port"]), authkey=bytes.fromhex(runtime["key"]))
            connections.append((connection, worker))
            connection.send({"action": "snapshot"})
            if not connection.poll(5):
                raise RuntimeError("Unable to verify the current worker state")
            snapshot = connection.recv()
            active = [s for s in snapshot.get("signals", []) if s["state"] in {"open", "pending", "sending", "uncertain"}]
            if active:
                raise RuntimeError("Worker has active signals or trades; stop it from the application before updating")
        # Keep the previous files recoverable before closing any process.
        if not backup.exists():
            shutil.copytree(target, backup)
        desktop_ids = {p.pid for p in desktops}
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        user32.GetWindowThreadProcessId.restype = ctypes.c_ulong
        user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
        user32.PostMessageW.restype = ctypes.c_int
        user32.EnumWindows.argtypes = [callback_type, ctypes.c_void_p]
        user32.EnumWindows.restype = ctypes.c_int
        def close_window(hwnd, _):
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in desktop_ids:
                user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
            return True
        callback = callback_type(close_window)
        user32.EnumWindows(callback, 0)
        for desktop in desktops:
            desktop.wait(timeout=8)
        for connection, worker in connections:
            connection.send({"action": "stop"})
            if not connection.poll(5):
                raise RuntimeError("Worker did not acknowledge shutdown")
            connection.recv()
            worker.wait(timeout=10)
        try:
            shutil.copytree(source, target, dirs_exist_ok=True)
            # Remove conflicting ICU files left by older build environments.
            for filename in ("icuuc.dll", "icudt78.dll"):
                obsolete = target / "_internal" / filename
                if obsolete.is_file() and not (source / "_internal" / filename).exists():
                    obsolete.unlink()
        except Exception:
            shutil.copytree(backup, target, dirs_exist_ok=True)
            raise
        if args.restart:
            subprocess.Popen([str(executable), "--mode", "live", "--page", "6"] + (["--engine-stopped"] if args.engine_stopped else []), cwd=str(project),
                             creationflags=subprocess.DETACHED_PROCESS,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("Updated the existing GoldSignalDesk-0.1.1 installation in place; data and previous entry setting preserved.")
    finally:
        for connection, _ in connections:
            connection.close()


if __name__ == "__main__":
    main()
