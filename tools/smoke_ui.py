from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

project = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QUICK_BACKEND"] = "software"

from PySide6.QtCore import Qt, QUrl, qInstallMessageHandler, QObject, QMetaObject, Q_ARG
from PySide6.QtGui import QFontDatabase
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication
from signaldesk.desktop import Bridge
from signaldesk.reports import build_report


def main():
    warnings = []
    def handler(kind, context, message):
        warnings.append(message)
    qInstallMessageHandler(handler)
    QQuickStyle.setStyle("Fusion")
    app = QApplication([])
    app.setLayoutDirection(Qt.RightToLeft)
    for name in ("segoeui.ttf", "seguisb.ttf", "segoeuib.ttf"):
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/"+name)
    bridge = Bridge(offline=True)
    sample = json.loads((project / ".video-review" / "service-snapshot.json").read_text(encoding="utf-8"))
    latest = max((project / ".local-data").glob("smoke-*/demo.sqlite"), key=lambda p:p.stat().st_mtime)
    sample["report"] = build_report(latest, "2020-01-01", "2030-01-01")
    bridge.update(json.loads(json.dumps(sample)))
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(QUrl.fromLocalFile(str(project / "signaldesk" / "qml" / "Main.qml")))
    assert engine.rootObjects(), warnings
    window = engine.rootObjects()[0]
    # Repeated IPC snapshots must not reset a user's uncommitted choice.
    import queue
    bridge.client = type("FakeClient", (), {"commands": queue.Queue()})()
    sample["channels"] = [{"id": -1001001, "name": "القناة الأولى"}, {"id": -1001002, "name": "القناة الثانية"}]
    sample["symbols"] = [{"name": "EURUSD", "visible": True}, {"name": "XAUUSD", "visible": True}, {"name": "GBPUSD", "visible": False}, {"name": "BTCUSD.m", "description": "Bitcoin / US Dollar", "visible": True}]
    sample["settings"]["channel_id"] = -1001001
    sample["settings"]["channel_name"] = "القناة الأولى"
    sample["connected"] = False
    from signaldesk import __version__, ENGINE_PROTOCOL
    sample["version"] = __version__
    sample["engine_protocol"] = ENGINE_PROTOCOL
    bridge.update(json.loads(json.dumps(sample)))
    window.setProperty("page", 6)
    app.processEvents()
    channels = window.findChild(QObject, "channelCombo")
    symbols = window.findChild(QObject, "symbolCombo")
    symbol_input = window.findChild(QObject, "tradingSymbol")
    label = window.findChild(QObject, "savedChannelLabel")
    assert channels.property("count") == 2
    assert symbols.property("count") == 4, (symbols.property("count"), warnings)
    channels.setProperty("currentIndex", 1)
    QMetaObject.invokeMethod(channels, "activated", Q_ARG(int, 1))
    symbols.setProperty("currentIndex", 0)
    QMetaObject.invokeMethod(symbols, "activated", Q_ARG(int, 0))
    for _ in range(10):
        bridge.update(json.loads(json.dumps(sample)))
        app.processEvents()
        assert channels.property("currentIndex") == 1
        assert symbol_input.property("text") == "EURUSD"
    search = window.findChild(QObject, "symbolSearchInput")
    search.setProperty("text", "bTc")
    QMetaObject.invokeMethod(search, "textEdited")
    app.processEvents()
    assert symbols.property("count") == 1
    assert symbol_input.property("text") == "EURUSD"  # Searching must never switch the trading symbol.
    symbols.setProperty("currentIndex", 0)
    QMetaObject.invokeMethod(symbols, "activated", Q_ARG(int, 0))
    assert symbol_input.property("text") == "BTCUSD.m"
    bridge.update(json.loads(json.dumps(sample)))
    app.processEvents()
    assert symbols.property("count") == 1 and symbol_input.property("text") == "BTCUSD.m"
    search.setProperty("text", "bitcoin")
    QMetaObject.invokeMethod(search, "textEdited")
    app.processEvents()
    assert symbols.property("count") == 1
    search.setProperty("text", "no-such-symbol")
    QMetaObject.invokeMethod(search, "textEdited")
    app.processEvents()
    assert symbols.property("count") == 0
    search.setProperty("text", "")
    QMetaObject.invokeMethod(search, "textEdited")
    sample["channels"].reverse()
    bridge.update(json.loads(json.dumps(sample)))
    app.processEvents()
    assert channels.property("currentIndex") == 0
    QMetaObject.invokeMethod(window.findChild(QObject, "chooseChannelButton"), "clicked")
    command = bridge.client.commands.get_nowait()
    assert command["action"] == "choose_channel" and command["id"] == -1001002
    sample["settings"].update(channel_id=-1001002, channel_name="القناة الثانية", symbol="BTCUSD.m")
    bridge.update(json.loads(json.dumps(sample)))
    app.processEvents()
    assert "القناة الثانية" in label.property("text")
    second_combo = window.findChild(QObject, "secondChannelCombo")
    assert second_combo.property("count") == 1
    QMetaObject.invokeMethod(window.findChild(QObject, "chooseSecondChannelButton"), "clicked")
    command = bridge.client.commands.get_nowait()
    assert command["action"] == "choose_second_channel" and command["id"] == -1001001
    sample["settings"].update(second_channel_id=-1001001, second_channel_name="القناة الأولى", shared_channel_settings=True)
    sample["monitored_channels"] = [{"id": -1001002, "name": "القناة الثانية", "ready": True, "error": ""},
                                    {"id": -1001001, "name": "القناة الأولى", "ready": True, "error": ""}]
    bridge.update(json.loads(json.dumps(sample)))
    app.processEvents()
    assert "القناة الأولى" in window.findChild(QObject, "savedSecondChannelLabel").property("text")
    QMetaObject.invokeMethod(window.findChild(QObject, "removeSecondChannelButton"), "clicked")
    assert bridge.client.commands.get_nowait()["action"] == "remove_second_channel"
    sample.update(mode="live", connected=True, quote_ready=False, quote_error="سعر الرمز قديم؛ بانتظار تحديث من الوسيط",
                  account={"login": 123, "trade_allowed": True, "hedging": True}, paused=True,
                  telegram="متصل", telegram_connected=True, channel_listening=True)
    bridge.update(json.loads(json.dumps(sample)))
    app.processEvents()
    assert "تم الاتصال" in window.findChild(QObject, "mt5StatusLabel").property("text")
    assert "تم الاتصال" in window.findChild(QObject, "telegramStatusLabel").property("text")
    assert "مفعّلة" in window.findChild(QObject, "channelListeningLabel").property("text")
    assert window.findChild(QObject, "entryToggle").property("enabled") is True
    QMetaObject.invokeMethod(window.findChild(QObject, "entryToggle"), "clicked")
    QMetaObject.invokeMethod(window.findChild(QObject, "enableEntryDialog"), "accept")
    command = bridge.client.commands.get_nowait()
    assert command == {"action": "pause", "paused": False, "account_id": sample["account_id"]}
    sample.update(connected=False, mt5_error="سبب فشل MT5 للاختبار", telegram_connected=False, channel_listening=False,
                  telegram_error="رمز التحقق غير صحيح")
    bridge.update(json.loads(json.dumps(sample)))
    app.processEvents()
    assert "سبب فشل MT5" in window.findChild(QObject, "mt5ErrorLabel").property("text")
    assert "رمز التحقق" in window.findChild(QObject, "telegramErrorLabel").property("text")
    assert "غير جاهزة" in window.findChild(QObject, "channelListeningLabel").property("text")
    QMetaObject.invokeMethod(window.findChild(QObject, "entryToggle"), "clicked")
    assert "سبب فشل MT5" in window.property("message")
    # A different account must discard uncommitted choices and cached report/trade data.
    window.setProperty("dirty", True)
    QMetaObject.invokeMethod(window.findChild(QObject, "enableEntryDialog"), "open")
    window.setProperty("symbolSearch", "bitcoin")
    different = json.loads(json.dumps(sample))
    different.update(account_id="test:222", connected=True, quote_ready=True, symbol="EURUSD",
                     account={"login":222,"server":"test","hedging":True,"trade_allowed":True},
                     signals=[], events=[], report={}, mt5_error="", quote_error="")
    different["settings"].update(symbol="EURUSD", channel_id=-1001001, channel_name="القناة الأولى", second_channel_id=0, second_channel_name="")
    bridge.update(different)
    app.processEvents()
    assert not window.property("dirty") and window.property("symbolSearch") == ""
    assert not window.findChild(QObject, "enableEntryDialog").property("visible")
    assert symbol_input.property("text") == "EURUSD"
    help_label = window.findChild(QObject, "symbolHelpLabel").property("text")
    assert "EURUSD" in help_label and "BTCUSD" not in help_label
    window.setProperty("page", 0)
    app.processEvents()
    dashboard = window.findChild(QObject, "pageLoader").property("item")
    assert dashboard.findChild(QObject, "connectedSymbolLabel").property("text") == "EURUSD"
    bridge.update({"engine_connected":False})
    app.processEvents()
    assert not bridge.data["connected"] and bridge.data["report"] == {} and bridge.data["signals"] == []
    assert bridge.data["monitored_channels"] == []
    assert "BTC" not in dashboard.findChild(QObject, "connectedSymbolLabel").property("text")
    bridge.update({"engine_connected":False, "engine_state":"stopping"})
    app.processEvents()
    assert "جارٍ إيقاف" in window.findChild(QObject, "engineStatusLabel").property("text")
    bridge.update({"engine_connected":False, "engine_state":"stopped"})
    window.setProperty("page", 6)
    app.processEvents()
    assert "المحرك متوقف" in window.findChild(QObject, "engineStatusLabel").property("text")
    connections_page = window.findChild(QObject, "pageLoader").property("item")
    assert connections_page.findChild(QObject, "startEngineButton").property("visible")
    assert not connections_page.findChild(QObject, "stopEngineButton").property("enabled")
    sample.update(mode="demo", connected=True, mt5_error="", quote_error="", telegram_error="", telegram_connected=True, channel_listening=True)
    bridge.update(json.loads(json.dumps(sample)))
    bridge.client = None
    for page in range(8):
        window.setProperty("page", page)
        for _ in range(20):
            app.processEvents()
            time.sleep(.02)
        assert window.grabWindow().save(str(project / ".video-review" / f"ui-page-{page}.png"))
    window.setProperty("page", 6)
    app.processEvents()
    for item in window.findChildren(QObject):
        if item.inherits("QQuickFlickable") and item.property("contentHeight") > item.property("height"):
            item.setProperty("contentY", item.property("contentHeight") - item.property("height"))
    for _ in range(10):
        app.processEvents()
        time.sleep(.02)
    assert window.grabWindow().save(str(project / ".video-review" / "ui-channel-choice.png"))
    bridge._export_pdf(str(project / ".video-review" / "smoke-report.pdf"))
    assert (project / ".video-review" / "smoke-report.pdf").stat().st_size > 1000
    serious = [w for w in warnings if any(k in w for k in ("ReferenceError", "TypeError", "Cannot assign", "failed to load", "Unable to assign"))]
    assert not serious, serious
    window.close()
    app.processEvents()
    print("UI smoke passed: account switch/dirty choices/cache clearing; actual symbol labels; symbol search/name/description/no-results; connection success/failure; entry activation with stale quote; channel persistence; all 8 pages rendered; Arabic PDF; no QML binding errors.")


if __name__ == "__main__":
    main()
