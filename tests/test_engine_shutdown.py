import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
import psutil
from PySide6.QtWidgets import QApplication
from signaldesk import desktop


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("lost_ack", [False, True])
def test_stop_prioritizes_shutdown_and_never_launches_worker(monkeypatch, app, lost_ack):
    sent, states, waits = [], [], []
    class Connection:
        def send(self, command):
            sent.append(command)
        def poll(self, timeout):
            return True
        def recv(self):
            if lost_ack:
                raise EOFError()
            return {"connected": True}
        def close(self):
            pass
    class Process:
        def __init__(self, pid):
            assert pid == 1234
        def wait(self, timeout):
            waits.append(timeout)
    monkeypatch.setattr(desktop, "load_secret", lambda path: {"port": 123, "key": "aa", "pid": 1234})
    monkeypatch.setattr(desktop, "Client", lambda *a, **k: Connection())
    monkeypatch.setattr(psutil, "Process", Process)
    monkeypatch.setattr(desktop, "launch_engine", lambda mode: pytest.fail("Shutdown relaunched the engine"))
    client = desktop.EngineClient()
    client.received.connect(states.append)
    client.commands.put({"action": "demo_signal", "text": "اشتري 4194"})
    client.request_engine_stop()
    client.run()
    assert sent == [{"action": "stop"}]
    assert waits == [10]
    assert states == [{"engine_connected": False, "engine_state": "stopped"}]
    assert not client.running


def test_failed_shutdown_does_not_claim_stopped_or_restart(monkeypatch, app):
    states = []
    monkeypatch.setattr(desktop, "load_secret", lambda path: {"port": 123, "key": "aa", "pid": 1234})
    def refused(*a, **k):
        raise ConnectionRefusedError()
    monkeypatch.setattr(desktop, "Client", refused)
    class Process:
        def __init__(self, pid):
            pass
        def wait(self, timeout):
            raise psutil.TimeoutExpired(timeout)
    monkeypatch.setattr(psutil, "Process", Process)
    monkeypatch.setattr(desktop, "launch_engine", lambda mode: pytest.fail("Failed shutdown relaunched worker"))
    client = desktop.EngineClient()
    client.received.connect(states.append)
    client.request_engine_stop()
    client.run()
    assert states[0]["engine_state"] == "stop_failed"
    assert states[0]["stop_error"]


def test_stopped_bridge_clears_connection_and_blocks_commands_until_start(monkeypatch, app):
    bridge = desktop.Bridge(offline=True)
    bridge.update({"mode":"demo", "account_id": "test:1", "connected": True, "paused": False,
                   "signals": [{"state": "open"}], "telegram_connected": True})
    bridge.update({"engine_connected": False, "engine_state": "stopped"})
    assert bridge.data["engine_state"] == "stopped"
    assert not bridge.data["engine_connected"] and not bridge.data["connected"]
    assert not bridge.data["telegram_connected"] and bridge.data["paused"]
    assert bridge.data["signals"] == []
    notices = []
    bridge.toast.connect(notices.append)
    bridge.send('{"action":"pause","paused":false}')
    assert "المحرك متوقف" in notices[-1]
    starts = []
    monkeypatch.setattr(bridge, "start_client", starts.append)
    bridge.startEngine()
    assert starts == ["demo"]


def test_inflight_snapshot_cannot_restore_connected_after_stop(monkeypatch, app):
    states = []
    runtime = {"port": 123, "key": "aa", "pid": 1234}
    client = desktop.EngineClient()
    class Connection:
        def send(self, cmd):
            pass
        def poll(self, timeout):
            return True
        def recv(self):
            client.request_engine_stop()
            return {"connected": True}
        def close(self):
            pass
    monkeypatch.setattr(desktop, "load_secret", lambda path: runtime)
    monkeypatch.setattr(desktop, "Client", lambda *a, **k: Connection())
    monkeypatch.setattr(client, "msleep", lambda ms: None)
    monkeypatch.setattr(client, "shutdown_engine", lambda *a: client.received.emit({"engine_connected": False, "engine_state": "stopped"}))
    client.received.connect(states.append)
    client.run()
    assert states == [{"engine_connected": False, "engine_state": "stopped"}]
