from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import time
import threading
from datetime import datetime, timedelta
from multiprocessing.connection import Client
from pathlib import Path

from PySide6.QtCore import QObject, Property, QThread, QTimer, Signal, Slot, QUrl, Qt
from PySide6.QtGui import QGuiApplication, QFontDatabase
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication, QFileDialog

from .config import Settings, data_directory
from . import __version__, ENGINE_PROTOCOL
from .security import load_secret


def launch_engine(mode):
    if getattr(sys, "frozen", False):
        args = [sys.executable, "--engine", "--mode", mode]
    else:
        executable = Path(sys.executable).with_name("pythonw.exe")
        args = [str(executable if executable.exists() else sys.executable), str(Path(__file__).parent.parent / "main.py"), "--engine", "--mode", mode]
    subprocess.Popen(args, cwd=str(Path(__file__).parent.parent), creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class EngineClient(QThread):
    received = Signal(dict)

    def __init__(self, mode="demo", parent=None):
        super().__init__(parent)
        self.mode = mode
        self.commands = queue.Queue()
        self.running = True
        self.stop_requested = threading.Event()

    def request_engine_stop(self):
        self.stop_requested.set()

    def shutdown_engine(self, connection, runtime):
        import psutil
        try:
            if connection is None:
                connection = Client(("127.0.0.1", runtime["port"]), authkey=bytes.fromhex(runtime["key"]))
            connection.send({"action": "stop"})
            if connection.poll(4):
                connection.recv()
        except (OSError, EOFError):
            # A lost acknowledgement is harmless only if the worker actually exits.
            pass
        finally:
            if connection:
                connection.close()
        try:
            psutil.Process(runtime["pid"]).wait(timeout=10)
        except psutil.NoSuchProcess:
            pass
        self.received.emit({"engine_connected": False, "engine_state": "stopped"})

    def run(self):
        connection = None
        launched = False
        next_launch = 0
        while self.running:
            if self.stop_requested.is_set():
                try:
                    runtime = load_secret(data_directory() / f"{self.mode}.runtime")
                    if runtime:
                        self.shutdown_engine(connection, runtime)
                    else:
                        # Without a worker identity, do not claim that shutdown succeeded.
                        raise ConnectionError("تعذر التحقق من هوية المحرك")
                except Exception as exc:
                    self.received.emit({"engine_connected": False, "engine_state": "stop_failed",
                                        "stop_error": "تعذر تأكيد إيقاف المحرك: " + str(exc)})
                self.running = False
                break
            try:
                if connection is None:
                    runtime = load_secret(data_directory() / f"{self.mode}.runtime")
                    if runtime:
                        connection = Client(("127.0.0.1", runtime["port"]), authkey=bytes.fromhex(runtime["key"]))
                    else:
                        raise ConnectionError("لا يوجد محرك")
                try:
                    command = self.commands.get_nowait()
                except queue.Empty:
                    command = {"action": "snapshot"}
                if command.get("action") == "stop":
                    self.request_engine_stop()
                if self.stop_requested.is_set():
                    continue
                connection.send(command)
                if not connection.poll(4):
                    raise ConnectionError("لم يصل تحديث من المحرك")
                snapshot = connection.recv()
                if not self.stop_requested.is_set():
                    self.received.emit(snapshot)
                self.msleep(180)
                launched = False
            except Exception:
                if connection:
                    connection.close()
                    connection = None
                if not self.stop_requested.is_set() and not launched and time.time() >= next_launch:
                    launch_engine(self.mode)
                    launched = True
                    next_launch = time.time() + 6
                if not self.stop_requested.is_set():
                    self.received.emit({"engine_connected": False})
                self.msleep(600)
                if time.time() >= next_launch:
                    launched = False
        if connection:
            connection.close()

    def stop(self):
        if self.stop_requested.is_set():
            # Closing the window immediately after Stop must still deliver the shutdown.
            self.wait(20000)
        else:
            self.running = False
            self.wait(5000)


class Bridge(QObject):
    changed = Signal()
    settingsChanged = Signal()
    toast = Signal(str)

    def __init__(self, mode="demo", offline=False, engine_stopped=False):
        super().__init__()
        self._data = {"mode": mode, "connected": False, "engine_connected": False, "signals": [], "events": [], "paused": True,
                      "account": {}, "tick": {}, "telegram": "غير متصل", "channels": [], "terminals": [], "symbols": [], "report": {}, "unresolved": 0}
        self._settings = Settings().to_dict()
        self.notice_id = 0
        self.pending_mode = None
        self.client = None
        self.version_warning = False
        self.pause_target = None
        self.pause_request_id = 0
        if engine_stopped:
            self.update({"engine_connected": False, "engine_state": "stopped"})
        elif not offline:
            self.start_client(mode)

    def start_client(self, mode):
        if self.client:
            self.client.received.disconnect()
            self.client.stop()
        self._data["mode"] = mode
        self.update({"engine_connected": False})
        self.client = EngineClient(mode, self)
        self.client.received.connect(self.update)
        self.client.start()

    @Property("QVariantMap", notify=changed)
    def data(self):
        return self._data

    @Property("QVariantMap", notify=settingsChanged)
    def settings(self):
        return self._settings

    @Property(str, constant=True)
    def today(self):
        return datetime.now().strftime("%Y-%m-%d")

    @Slot(dict)
    def update(self, data):
        # A queued snapshot from before the stop click must not restore the old status.
        if self._data.get("engine_state") == "stopping" and data.get("engine_connected") is not False:
            return
        if data.get("engine_connected") is False:
            self.pause_target = None
            self._data.update(engine_connected=False, connected=False, quote_ready=False,
                              account={}, tick={}, signals=[], events=[], report={}, symbol="",
                              paused=True, telegram="غير متصل", telegram_connected=False, channel_listening=False,
                              engine_state=data.get("engine_state", "connecting"), stop_error=data.get("stop_error", ""), entry_pending=False)
            if data.get("engine_state") == "stopped":
                self.toast.emit("تم إيقاف المحرك بالكامل؛ أوامر الستوب والهدف لدى الوسيط تبقى فعّالة")
            elif data.get("stop_error"):
                self.toast.emit(data["stop_error"])
        else:
            self._data = {**data, "engine_connected": True, "engine_state": "connected"}
            if self.pause_target is not None and (data.get("paused") == self.pause_target or data.get("notice_id", 0) != self.notice_id):
                self.pause_target = None
            self._data["entry_pending"] = self.pause_target is not None
            self._data["compatible"] = data.get("version") == __version__ and data.get("engine_protocol") == ENGINE_PROTOCOL
            if not self._data["compatible"] and self.client and not self.version_warning:
                self.version_warning = True
                self.toast.emit("محرك النسخة السابقة ما زال يعمل. أوقف المحرك بالكامل ثم أغلق التطبيق وافتح النسخة الجديدة.")
            settings = data.get("settings", self._settings)
            if settings != self._settings:
                self._settings = settings
                self.settingsChanged.emit()
            if data.get("notice_id", 0) != self.notice_id:
                self.notice_id = data.get("notice_id", 0)
                if data.get("notice"):
                    self.toast.emit(data["notice"])
        self.changed.emit()
        if self.pending_mode and self._data.get("paused") and self._data.get("engine_connected"):
            target, self.pending_mode = self.pending_mode, None
            QTimer.singleShot(0, lambda: self.start_client(target))

    @Slot(str)
    def send(self, payload):
        try:
            command = json.loads(payload)
            if self._data.get("engine_state") in {"stopped", "stopping", "stop_failed"}:
                self.toast.emit("المحرك متوقف أو جارٍ إيقافه؛ شغّله من صفحة الاتصالات أولًا")
                return
            if self._data.get("account_id"):
                command["account_id"] = self._data["account_id"]
            if self.client:
                if self._data.get("compatible") is False and command.get("action") != "stop":
                    self.toast.emit("أوقف محرك النسخة السابقة بالكامل ثم افتح النسخة الجديدة لتطبيق التحديث.")
                    return
                if command.get("action") == "pause":
                    if self.pause_target is not None:
                        return
                    self.pause_target = bool(command["paused"])
                    self.pause_request_id += 1
                    request_id = self.pause_request_id
                    self._data["entry_pending"] = True
                    self.changed.emit()
                    QTimer.singleShot(6000, lambda: self.check_pause_response(request_id))
                self.client.commands.put(command)
            else:
                self.toast.emit("معاينة الواجهة فقط")
        except ValueError:
            self.toast.emit("طلب غير صالح")

    def check_pause_response(self, request_id):
        if request_id == self.pause_request_id and self.pause_target is not None:
            self.pause_target = None
            self._data["entry_pending"] = False
            self.changed.emit()
            self.toast.emit("لم يصل تأكيد تغيير حالة الدخول؛ راجع اتصال المحرك وأعد المحاولة")

    @Slot(str)
    def saveSettings(self, payload):
        try:
            data = json.loads(payload)
            settings = Settings.from_dict(data["settings"])
            self.send(json.dumps({"action": "settings", "settings": settings.to_dict(), "cancel_previous": data.get("cancel_previous", False)}))
        except (ValueError, TypeError, KeyError) as exc:
            self.toast.emit(str(exc))

    @Slot(str)
    def switchMode(self, mode):
        if mode in {"demo", "live"}:
            if mode == self._data.get("mode"):
                return
            if self._data.get("engine_connected") and not self._data.get("paused", True):
                self.pending_mode = mode
                self.send(json.dumps({"action": "pause", "paused": True}))
                self.toast.emit("سيُوقف الدخول في الوضع السابق؛ إدارة الصفقات تستمر")
            else:
                self.start_client(mode)
                self.changed.emit()

    @Slot(str, result=str)
    def dateForPeriod(self, period):
        now = datetime.now()
        if period == "week":
            now -= timedelta(days=6)
        elif period == "month":
            now = now.replace(day=1)
        return now.strftime("%Y-%m-%d")

    @Slot(str)
    def exportReport(self, format):
        if format == "pdf":
            path, _ = QFileDialog.getSaveFileName(None, "حفظ التقرير", "تقرير التداول.pdf", "PDF (*.pdf)")
            if path:
                self._export_pdf(path)
            return
        extension = "xlsx" if format == "xlsx" else "csv"
        path, _ = QFileDialog.getSaveFileName(None, "حفظ التقرير", "تقرير التداول." + extension, f"{extension.upper()} (*.{extension})")
        if path:
            self.send(json.dumps({"action": "export", "path": path}))

    def _export_pdf(self, path):
        import html
        from PySide6.QtGui import QTextDocument, QPageSize
        from PySide6.QtPrintSupport import QPrinter
        from .reports import HEADERS
        report = self._data.get("report", {})
        if not report.get("start"):
            self.toast.emit("جهّز التقرير أولًا")
            return
        doc = QTextDocument()
        rows = "".join("<tr>" + "".join(f"<td>{html.escape(str(row.get(k, '')))}</td>" for k in HEADERS) + "</tr>" for row in report["rows"])
        headers = "".join(f"<th>{v}</th>" for v in HEADERS.values())
        doc.setHtml(f"""<html dir='rtl'><style>body{{font-family:'Segoe UI';font-size:9pt}}td,th{{padding:5px}}th{{background:#e8eee9}}h1{{color:#173d36}}</style><body>
          <h1>تقرير التداول — Gold Signal Desk</h1><p>{report['start']} — {report['end']} | {html.escape(report['timezone'])}</p>
          <p>الحساب: {html.escape(str(report.get('account') or self._data.get('account_id') or 'محاكاة'))}</p>
          <p>صافي الربح: {report['net']} | صفقات مغلقة: {report['closed_count']} | نسبة النجاح: {report['win_rate']}%</p>
          <table border='1' cellspacing='0' width='100%'><thead><tr>{headers}</tr></thead>{rows}</table></body></html>""")
        printer = QPrinter(QPrinter.HighResolution)
        printer.setOutputFormat(QPrinter.PdfFormat)
        printer.setOutputFileName(path)
        printer.setPageSize(QPageSize(QPageSize.A3))
        from PySide6.QtGui import QPageLayout
        printer.setPageOrientation(QPageLayout.Landscape)
        doc.print_(printer)
        self.toast.emit("تم حفظ PDF: " + path)

    @Slot()
    def stopEngine(self):
        # Stop only by an explicit button; closing the GUI keeps the independent service alive.
        if self._data.get("engine_state") in {"stopped", "stopping"}:
            return
        if self.client and self.client.isRunning():
            self.client.request_engine_stop()
            self.update({"engine_connected": False, "engine_state": "stopping"})
            self.toast.emit("جارٍ إيقاف المحرك والتأكد من توقفه")
        else:
            # Retry shutdown without enabling automatic worker launch.
            self.client = EngineClient(self._data["mode"], self)
            self.client.request_engine_stop()
            self.client.received.connect(self.update)
            self._data.update(engine_state="stopping", engine_connected=False)
            self.changed.emit()
            self.client.start()

    @Slot()
    def startEngine(self):
        if self._data.get("engine_state") == "stopped":
            self.start_client(self._data["mode"])

    def close(self):
        if self.client:
            self.client.stop()


def run_desktop(mode="demo", preview=None, page=0, engine_stopped=False):
    QQuickStyle.setStyle("Fusion")
    app = QApplication(sys.argv)
    app.setApplicationName("Gold Signal Desk")
    app.setOrganizationName("GoldSignalDesk")
    app.setLayoutDirection(Qt.RightToLeft)
    for filename in ("segoeui.ttf", "seguisb.ttf", "segoeuib.ttf"):
        font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / filename
        if font_path.exists():
            QFontDatabase.addApplicationFont(str(font_path))
    app.setFont(__import__("PySide6.QtGui", fromlist=["QFont"]).QFont("Segoe UI", 10))
    app.setQuitOnLastWindowClosed(True)
    bridge = Bridge(mode, offline=bool(preview), engine_stopped=engine_stopped)
    qml = QQmlApplicationEngine()
    qml.rootContext().setContextProperty("bridge", bridge)
    qml.load(QUrl.fromLocalFile(str(Path(__file__).parent / "qml" / "Main.qml")))
    if not qml.rootObjects():
        bridge.close()
        return 1
    window = qml.rootObjects()[0]
    window.setProperty("page", page)
    if preview:
        snapshot_path = Path(__file__).parent.parent / ".video-review" / "service-snapshot.json"
        if snapshot_path.exists():
            bridge.update(json.loads(snapshot_path.read_text(encoding="utf-8")))
        def capture():
            try:
                if not window.grabWindow().save(str(preview)):
                    raise RuntimeError("تعذر حفظ معاينة الواجهة")
            finally:
                app.quit()
        QTimer.singleShot(1200, capture)
    app.aboutToQuit.connect(bridge.close)
    return app.exec()
