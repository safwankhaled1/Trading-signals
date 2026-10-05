"""Exercise actual QML controls and client shutdown against an isolated demo worker."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

project = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QUICK_BACKEND"] = "software"
os.environ["SIGNALDESK_DATA_DIR"] = str(project / ".local-data" / f"controls-{time.time_ns()}")

import psutil
from PySide6.QtCore import QObject, QMetaObject, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWidgets import QApplication
from signaldesk import desktop
from signaldesk.security import load_secret


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable")
    args = parser.parse_args()
    workers = []
    def launch(mode):
        command = [args.executable] if args.executable else [sys.executable, str(project / "main.py")]
        workers.append(subprocess.Popen(command + ["--engine", "--mode", mode],
                                        creationflags=subprocess.CREATE_NO_WINDOW,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    desktop.launch_engine = launch
    app = QApplication([])
    bridge = desktop.Bridge("demo")
    qml = QQmlApplicationEngine()
    qml.rootContext().setContextProperty("bridge", bridge)
    qml.load(QUrl.fromLocalFile(str(project / "signaldesk/qml/Main.qml")))
    window = qml.rootObjects()[0]
    def wait_for(predicate):
        until = time.monotonic() + 20
        while time.monotonic() < until:
            app.processEvents()
            if predicate():
                return
            time.sleep(.03)
        raise AssertionError(json.dumps(bridge.data, ensure_ascii=True))
    try:
        wait_for(lambda: bridge.data.get("connected"))
        button = window.findChild(QObject, "entryToggle")
        assert button.property("text") == "إيقاف الدخول الجديد"
        QMetaObject.invokeMethod(button, "clicked")
        wait_for(lambda: bridge.data.get("paused") and not bridge.data.get("entry_pending"))
        assert button.property("text") == "تفعيل الدخول"
        QMetaObject.invokeMethod(button, "clicked")
        wait_for(lambda: not bridge.data.get("paused") and not bridge.data.get("entry_pending"))
        assert button.property("text") == "إيقاف الدخول الجديد"
        # Pausing must also work while the broker is temporarily disconnected.
        bridge._data["connected"] = False
        bridge.changed.emit()
        app.processEvents()
        QMetaObject.invokeMethod(button, "clicked")
        wait_for(lambda: bridge.data.get("paused") and not bridge.data.get("entry_pending"))
        window.setProperty("page", 6)
        app.processEvents()
        QMetaObject.invokeMethod(window.findChild(QObject, "stopEngineButton"), "clicked")
        dialog = window.findChild(QObject, "stopEngineDialog")
        QMetaObject.invokeMethod(dialog, "accept")
        wait_for(lambda: bridge.data.get("engine_state") == "stopped")
        assert not button.property("enabled")
        assert "المحرك متوقف" in window.findChild(QObject, "engineStatusLabel").property("text")
        runtime = load_secret(Path(os.environ["SIGNALDESK_DATA_DIR"]) / "demo.runtime")
        assert not psutil.pid_exists(runtime["pid"])
        for _ in range(60):
            app.processEvents()
            time.sleep(.03)
        assert len(workers) == 1 and workers[0].poll() is not None
        assert bridge.data["engine_state"] == "stopped"
        QMetaObject.invokeMethod(window.findChild(QObject, "startEngineButton"), "clicked")
        wait_for(lambda: bridge.data.get("connected"))
        assert bridge.data["paused"]  # Explicit restart retains disabled entry.
        bridge.stopEngine()
        bridge.close()  # An immediate window close must not cancel the stop request.
        wait_for(lambda: bridge.data.get("engine_state") == "stopped")
        print("Controls passed: entry pause/resume labels and broker disconnection; confirmed worker exit; no automatic restart; explicit restart retains paused entry; closing immediately still shuts down worker.")
    finally:
        bridge.close()
        window.close()
        for worker in workers:
            if worker.poll() is None:
                worker.terminate()
                worker.wait(timeout=5)


if __name__ == "__main__":
    main()
