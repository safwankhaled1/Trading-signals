import asyncio
from types import SimpleNamespace

import pytest

from signaldesk.service import Service
from signaldesk.broker import BrokerError, DemoBroker
from signaldesk.engine import TradingEngine


def test_channel_switch_restores_profile_and_keeps_global_connection(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNALDESK_DATA_DIR", str(tmp_path))
    service = Service("demo")
    try:
        service.settings.channel_id = -1001
        service.settings.channel_name = "أ"
        service.settings.fixed_lot = .03
        service.settings.terminal_path = "selected-terminal.exe"
        previous = service.settings.to_dict()
        service.store.set("profile:-1002", {**previous,"channel_id":-1002,"channel_name":"ب","fixed_lot":.07,"terminal_path":"old-terminal.exe"})
        asyncio.run(service.command({"action":"choose_channel","id":-1002,"name":"ب","cancel_previous":False,"settings":previous}))
        assert service.settings.fixed_lot == .07
        assert service.settings.terminal_path == "selected-terminal.exe"
        assert service.store.get("profile:-1001")["fixed_lot"] == .03
        asyncio.run(service.command({"action":"choose_channel","id":-1001,"name":"أ"}))
        assert service.settings.fixed_lot == .03
        assert service.settings.channel_id == -1001
    finally:
        service.store.close()
        service.pool.shutdown()


@pytest.mark.parametrize("disconnected", [False, True])
def test_channel_confirmation_and_restart_without_mt5(tmp_path, monkeypatch, disconnected):
    monkeypatch.setenv("SIGNALDESK_DATA_DIR", str(tmp_path))
    service = Service("live")
    try:
        if disconnected:
            service.connect_broker = lambda: None
            def disconnected_account():
                raise BrokerError("اتصال MT5 غير متاح")
            service.broker = SimpleNamespace(symbol="EURUSD", account=disconnected_account)
            service.engine = SimpleNamespace(paused=True, current_signals=lambda: [],
                                             apply_settings=lambda cfg: service.store.set("settings", cfg.to_dict()))
            def broken_snapshot():
                raise BrokerError("stale quote")
            service.engine.snapshot = broken_snapshot
        for identifier, name in [(-1001001, "أ"), (-1001002, "ب")]:
            asyncio.run(service.command({"action": "choose_channel", "id": identifier, "name": name}))
            service.snapshot()
            assert service.cache["settings"]["channel_id"] == identifier
            assert service.cache["settings"]["channel_name"] == name
            assert service.gateway.channel_id == identifier
            assert service.cache["connected"] is False
    finally:
        service.store.close()
        service.pool.shutdown()
    restored = Service("live")
    try:
        assert restored.settings.channel_id == -1001002
        assert restored.settings.channel_name == "ب"
    finally:
        restored.store.close()
        restored.pool.shutdown()


def test_stale_quote_preserves_account_and_allows_activation(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNALDESK_DATA_DIR", str(tmp_path))
    service = Service("live")
    try:
        service.broker = DemoBroker(service.store)
        service.engine = TradingEngine(service.store, service.broker, service.settings)
        service.engine.paused = True
        def stale_tick():
            raise BrokerError("سعر الرمز قديم؛ بانتظار تحديث من الوسيط")
        monkeypatch.setattr(service.broker, "tick", stale_tick)
        service.snapshot()
        assert service.cache["connected"] is True
        assert service.cache["account"]["balance"] == 10000
        assert service.cache["quote_ready"] is False
        assert "قديم" in service.cache["quote_error"]
        asyncio.run(service.command({"action": "pause", "paused": False}))
        assert service.engine.paused is False
        service.engine.receive(-1001, 1, "اشتري ذهب من 4154")
        assert not service.broker.open_positions  # A stale price can never open an order.
    finally:
        service.store.close()
        service.pool.shutdown()


def test_missing_broker_shows_activation_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNALDESK_DATA_DIR", str(tmp_path))
    service = Service("live")
    try:
        service.mt5_error = "تعذر الاتصال بالمنصة"
        with pytest.raises(BrokerError, match="تعذر الاتصال"):
            asyncio.run(service.command({"action": "pause", "paused": False}))
        service.snapshot()
        assert service.cache["mt5_error"] == "تعذر الاتصال بالمنصة"
    finally:
        service.store.close()
        service.pool.shutdown()


def test_telegram_failure_reason_and_success_state(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNALDESK_DATA_DIR", str(tmp_path))
    service = Service("demo")
    try:
        class PhoneCodeInvalidError(Exception):
            pass
        async def fail(*args):
            raise PhoneCodeInvalidError()
        service.gateway.sign_in = fail
        with pytest.raises(ValueError, match="رمز التحقق غير صحيح"):
            asyncio.run(service.command({"action": "telegram_login", "code": "wrong"}))
        service.snapshot()
        assert service.cache["telegram_error"] == "رمز التحقق غير صحيح"
        assert service.cache["telegram_connected"] is False
        assert service.cache["telegram_busy"] is False
        async def succeed(*args):
            service.gateway.authorized = True
            service.gateway.state = "متصل"
            service.gateway.client = SimpleNamespace(is_connected=lambda: True)
        service.gateway.sign_in = succeed
        asyncio.run(service.command({"action": "telegram_login", "code": "valid"}))
        service.snapshot()
        assert service.cache["telegram_connected"] is True
        assert service.cache["telegram_error"] == ""
        service.gateway.client = SimpleNamespace(is_connected=lambda: False)
        service.snapshot()
        assert service.cache["telegram_connected"] is False
        assert "انقطع" in service.cache["telegram_error"]
    finally:
        service.store.close()
        service.pool.shutdown()
def test_discovery_exposes_forex_and_closes_temporary_mt5_in_demo(tmp_path, monkeypatch):
    import sys
    monkeypatch.setenv("SIGNALDESK_DATA_DIR", str(tmp_path))
    shutdown = []
    symbols = [SimpleNamespace(name=name, description=name, visible=True) for name in ("XAUUSD", "EURUSD")]
    monkeypatch.setitem(sys.modules, "MetaTrader5", SimpleNamespace(initialize=lambda *a, **kw: True,
                          symbols_get=lambda: symbols, shutdown=lambda: shutdown.append(True)))
    monkeypatch.setattr("signaldesk.service.discover_terminals", lambda: ["test-terminal.exe"])
    service = Service("demo")
    try:
        service.connect_broker()
        asyncio.run(service.command({"action": "discover"}))
        assert {s["name"] for s in service.symbols} == {"EURUSD", "XAUUSD"}
        assert shutdown == [True]
    finally:
        service.store.close()
        service.pool.shutdown()


@pytest.mark.parametrize("action", ["settings", "connect_mt5"])
def test_symbol_change_with_open_trade_cannot_persist_wrong_symbol(tmp_path, monkeypatch, action):
    monkeypatch.setenv("SIGNALDESK_DATA_DIR", str(tmp_path))
    service = Service("live")
    try:
        service.settings.symbol = "XAUUSD"
        service.store.set("settings", service.settings.to_dict())
        service.broker = DemoBroker(service.store)
        service.broker.path = ""
        service.engine = TradingEngine(service.store, service.broker, service.settings)
        service.engine.receive(-1001, 1, "اشتري ذهب من 4154 ستوب 4153")
        assert service.broker.positions()
        command = {"action": action, "symbol": "EURUSD", "settings": {**service.settings.to_dict(), "symbol": "EURUSD"}}
        with pytest.raises(BrokerError):
            asyncio.run(service.command(command))
        assert service.settings.symbol == "XAUUSD"
        assert service.store.get("settings")["symbol"] == "XAUUSD"
    finally:
        service.store.close()
        service.pool.shutdown()
