"""Type decimal values into real QML controls with isolated settings, without trading."""
import argparse
import json
import os
from pathlib import Path
import queue
import sys

project = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QUICK_BACKEND"] = "software"

from PySide6.QtCore import QObject, QMetaObject, Qt, QUrl, Q_ARG, qInstallMessageHandler
from PySide6.QtGui import QInputMethodEvent
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from signaldesk import __version__, ENGINE_PROTOCOL
from signaldesk.config import Settings
from signaldesk.desktop import Bridge


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--qml", default=str(project / "signaldesk/qml/Main.qml"))
    args = parser.parse_args()
    warnings = []
    qInstallMessageHandler(lambda kind, context, message: warnings.append(message))
    app = QApplication([])
    app.setLayoutDirection(Qt.RightToLeft)
    bridge = Bridge(offline=True)
    sample = dict(mode="demo", settings=Settings().to_dict(), account_id="numeric-test",
                  version=__version__, engine_protocol=ENGINE_PROTOCOL, engine_connected=True,
                  engine_state="running", connected=True, paused=True, entry_pending=False,
                  account={}, tick={}, quote_ready=False, channels=[], symbols=[], events=[], signals=[], report={})
    bridge.update(json.loads(json.dumps(sample)))
    bridge.client = type("FakeClient", (), {"commands": queue.Queue()})()
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(QUrl.fromLocalFile(str(Path(args.qml).resolve())))
    assert engine.rootObjects(), warnings
    window = engine.rootObjects()[0]
    window.setProperty("page", 5)
    app.processEvents()

    def field(key):
        result = window.findChild(QObject, "numeric_" + key)
        assert result is not None, key
        return result

    def clear(control):
        QMetaObject.invokeMethod(control, "forceActiveFocus")
        app.processEvents()
        assert control.property("activeFocus")
        QTest.keyClick(window, Qt.Key_A, Qt.ControlModifier)
        QTest.keyClick(window, Qt.Key_Backspace)
        app.processEvents()
        assert control.property("text") == ""

    def type_value(key, text):
        control = field(key)
        clear(control)
        prefix = ""
        for char in text:
            QTest.keyClick(window, Qt.Key(ord(char.upper())))
            prefix += char
            app.processEvents()
            assert control.property("text") == prefix, (key, prefix, control.property("text"))
            # Frequent engine snapshots must not consume a decimal point or steal focus.
            bridge.update(json.loads(json.dumps(sample)))
            app.processEvents()
            assert control.property("text") == prefix and control.property("activeFocus")
        return control

    def save():
        # Save with the input still focused, including click handlers invoked before blur.
        QMetaObject.invokeMethod(window.findChild(QObject, "saveSettingsButton"), "clicked")
        app.processEvents()
        return bridge.client.commands.get_nowait()

    try:
        type_value("fixed_lot", "0.04")
        type_value("stop_distance", "0.5")
        command = save()
        assert command["settings"]["fixed_lot"] == .04
        assert command["settings"]["stop_distance"] == .5
        restored = Settings.from_dict(command["settings"]).to_dict()
        sample["settings"] = restored
        bridge.update(json.loads(json.dumps(sample)))
        QMetaObject.invokeMethod(window.contentItem(), "forceActiveFocus")
        app.processEvents()
        assert field("fixed_lot").property("text") == "0.04"
        assert field("stop_distance").property("text") == "0.5"
        for value in ("0.03", "0.5", "1.25", ".04", "0,04"):
            type_value("fixed_lot", value)
            assert save()["settings"]["fixed_lot"] == float(value.replace(",", "."))
        type_value("stop_distance", "0.04")
        assert save()["settings"]["stop_distance"] == .04
        type_value("report_utc_offset", "-3.5")
        assert save()["settings"]["report_utc_offset"] == -3.5
        arabic = field("fixed_lot")
        clear(arabic)
        event = QInputMethodEvent()
        event.setCommitString("٠٫٠٤")
        QApplication.sendEvent(window, event)
        app.processEvents()
        assert arabic.property("text") == "٠٫٠٤"
        assert save()["settings"]["fixed_lot"] == .04
        type_value("fixed_lot", ".")
        QMetaObject.invokeMethod(window.findChild(QObject, "saveSettingsButton"), "clicked")
        app.processEvents()
        assert bridge.client.commands.empty(), "Invalid text must not save a previous lot"
        # Switching account discards the focused draft and restores that account's settings.
        type_value("fixed_lot", "0.04")
        mode = window.findChild(QObject, "choice_entry_mode")
        assert mode.property("currentIndex") == 0 and field("entry_margin").property("enabled")
        mode.setProperty("currentIndex", 1)
        QMetaObject.invokeMethod(mode, "activated", Q_ARG(int, 1))
        app.processEvents()
        assert save()["settings"]["entry_mode"] == "direct"
        assert not field("entry_margin").property("enabled")
        assert "ستوب القناة" in window.findChild(QObject, "entryModeHelp").property("text")
        mode.setProperty("currentIndex", 0)
        QMetaObject.invokeMethod(mode, "activated", Q_ARG(int, 0))
        app.processEvents()
        assert save()["settings"]["entry_mode"] == "range"
        assert field("entry_margin").property("enabled")
        type_value("fixed_lot", "0.04")
        other = json.loads(json.dumps(sample))
        other["account_id"] = "other-numeric-test"
        other["settings"]["fixed_lot"] = .07
        bridge.update(other)
        app.processEvents()
        assert field("fixed_lot").property("text") == "0.07"
        assert not field("fixed_lot").property("activeFocus")
        serious = [w for w in warnings if any(word in w for word in
                   ("ReferenceError", "TypeError", "Cannot assign", "Unable to assign", "Binding loop"))]
        assert not serious, serious
        print("Settings input passed: decimal keystrokes and snapshots, focused save, restore, Arabic decimals, invalid text, direct/range selection and margin enablement, account switch; no broker commands.")
    finally:
        bridge.client = None
        window.close()
        app.processEvents()


if __name__ == "__main__":
    main()
