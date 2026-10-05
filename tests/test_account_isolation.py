import asyncio
import json
import sqlite3
from concurrent.futures import Future

import pytest

from signaldesk.broker import AccountChanged, DemoBroker
from signaldesk.config import Settings
from signaldesk.engine import TradingEngine
from signaldesk.reports import build_report
from signaldesk.service import Service
from signaldesk.store import Store


def test_accounts_separate_messages_profiles_inbox_and_history(tmp_path):
    store = Store(tmp_path / "accounts.sqlite")
    try:
        a = DemoBroker(store)
        a.identity = "server:A"
        first = TradingEngine(store, a)
        aid = first.receive(-1001, 1, "اشتري ذهب من 4154 ستوب 4153")
        first.apply_settings(Settings(fixed_lot=.06, channel_id=-1001))
        store.set("inbox", [["A"]])
        a.set_price(first.signals[aid]["fill"] + 5)
        first.step()
        first.sync_history()
        aevents = store.events()
        b = DemoBroker(store)
        b.identity, b.open_positions, b.history = "server:B", [], []
        second = TradingEngine(store, b)
        assert not second.signals and not store.events() and not store.deals()
        assert store.message(-1001, 1) is None
        assert store.get("inbox", []) == []
        assert second.settings_for(-1001).fixed_lot == .03
        b.bid = 4154
        bid = second.receive(-1001, 1, "اشتري ذهب من 4154 ستوب 4153")
        assert bid != aid and second.signals[bid]["state"] == "open"
        second.apply_settings(Settings(fixed_lot=.09, channel_id=-1001))
        assert store.get("profile:-1001")["fixed_lot"] == .09
        store.activate_account("server:A")
        assert store.get("profile:-1001")["fixed_lot"] == .06
        assert store.get("inbox") == [["A"]]
        assert store.message(-1001, 1)["signal"] == aid
        assert store.events() == aevents
        assert {s["id"] for s in store.signals()} == {aid}
        assert {d["signal"] for d in store.deals()} == {aid}
    finally:
        store.close()


@pytest.fixture
def service_accounts(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNALDESK_DATA_DIR", str(tmp_path))
    service = Service("live")
    state = {"account": "server:A"}

    def create(path="", symbol="", account_settings=None):
        broker = DemoBroker(service.store)
        broker.identity = state["account"]
        broker.open_positions, broker.history = [], []
        broker.path = path
        broker.symbol = (account_settings(broker.identity) if account_settings else symbol) or "XAUUSD"
        account = broker.account
        tick = broker.tick

        def checked_account():
            if state["account"] != broker.identity:
                raise AccountChanged("account switched")
            return {**account(), "login": broker.identity, "server": "test"}

        def checked_tick():
            checked_account()
            return tick()

        broker.account, broker.tick = checked_account, checked_tick
        broker.symbols = lambda: []
        return broker

    monkeypatch.setattr("signaldesk.service.MT5Broker", create)
    service.connect_broker()
    yield service, state
    service.store.close()
    service.pool.shutdown()


def test_service_switch_clears_report_and_restores_account_settings(service_accounts):
    service, state = service_accounts
    asyncio.run(service.command({"action": "choose_channel", "id": -1001, "name": "A"}))
    a_settings = {**service.settings.to_dict(), "fixed_lot": .06, "symbol": "BTCUSD"}
    asyncio.run(service.command({"action": "settings", "settings": a_settings}))
    assert service.broker.symbol == service.settings.symbol == "BTCUSD"
    service.store.event("account A event")
    service.store.set("inbox", [["deferred A"]])
    service.cache["report"] = {"net": 99, "start": "2020-01-01"}
    service.report_future = Future()
    old_report = service.report_future
    state["account"] = "server:B"
    service.connect_broker()
    service.snapshot()
    assert service.cache["account_id"] == "server:B"
    assert service.cache["report"] == {} and old_report.cancelled()
    assert service.cache["paused"] and service.cache["signals"] == []
    assert service.settings.symbol == service.broker.symbol == "XAUUSD"
    assert service.settings.fixed_lot == .03 and service.settings.channel_id == 0
    assert not service.deferred and not service.gateway.baseline_ready
    assert all(e["message"] != "account A event" for e in service.cache["events"])
    state["account"] = "server:A"
    service.connect_broker()
    assert service.settings.symbol == service.broker.symbol == "BTCUSD"
    assert service.settings.fixed_lot == .06 and service.settings.channel_id == -1001
    assert service.deferred == [["deferred A"]]
    assert service.engine.paused  # Account transitions always require explicit activation.


def test_old_gui_command_cannot_activate_or_edit_new_account(service_accounts):
    service, state = service_accounts
    state["account"] = "server:B"
    service.connect_broker()
    for cmd in [{"action": "pause", "paused": False}, {"action": "settings", "settings": Settings(fixed_lot=.5).to_dict()},
                {"action": "cancel", "id": "old-signal"}]:
        with pytest.raises(ValueError, match="تغيّر الحساب"):
            asyncio.run(service.command({**cmd, "account_id": "server:A"}))
    assert service.engine.paused and service.settings.fixed_lot == .03


def test_snapshot_hides_old_account_and_failed_connection_data(service_accounts):
    service, state = service_accounts
    asyncio.run(service.command({"action": "pause", "paused": False}))
    service.engine.receive(-1001, 1, "اشتري ذهب من 4154 ستوب 4153")
    service.snapshot()
    assert service.cache["signals"]
    state["account"] = "server:B"
    service.snapshot()
    assert not service.cache["signals"] and not service.cache["events"]
    assert service.cache["account"] == {} and service.cache["symbol"] == ""
    assert service.cache["paused"]
    service.engine = None
    service.snapshot()
    assert service.cache["signals"] == [] and service.cache["unresolved"] == 0


def test_running_service_detects_account_change_and_requires_activation(service_accounts):
    from types import SimpleNamespace
    service, state = service_accounts
    service.listen = lambda: setattr(service, "listener", SimpleNamespace(close=lambda: None))

    async def run():
        async def switch():
            try:
                await asyncio.sleep(.2)
                state["account"] = "server:B"
                for _ in range(15):
                    if service.store.account == "server:B" and service.cache.get("account_id") == "server:B":
                        break
                    await asyncio.sleep(.1)
                assert service.store.account == "server:B"
                assert service.cache["account_id"] == "server:B"
                assert service.engine.paused
            finally:
                service.running = False
        await asyncio.wait_for(asyncio.gather(service.run(), switch()), timeout=5)

    asyncio.run(run())


def test_legacy_migration_keeps_backup_and_filters_reports(tmp_path):
    path = tmp_path / "legacy.sqlite"
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE kv(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE signals(id TEXT PRIMARY KEY,data TEXT NOT NULL);
        CREATE TABLE events(id INTEGER PRIMARY KEY,time REAL,level TEXT,message TEXT,signal TEXT,details TEXT);
        CREATE TABLE messages(channel INTEGER,message INTEGER,text TEXT,reply INTEGER,signal TEXT,received REAL,PRIMARY KEY(channel,message));
        CREATE TABLE deals(id TEXT PRIMARY KEY,data TEXT NOT NULL);
    """)
    db.executemany("INSERT INTO kv VALUES (?,?)", [("last_account", json.dumps("server:B")), ("settings", json.dumps(Settings(fixed_lot=.07).to_dict()))])
    for index, owner in enumerate(("server:A", "server:B")):
        signal = {"id": owner, "account": owner, "config": Settings(fixed_lot=.03).to_dict(), "channel": -1001,
                  "created": 1800000000, "state": "closed", "closed": 1800000001}
        db.execute("INSERT INTO signals VALUES (?,?)", (owner, json.dumps(signal)))
        deal = {"id": owner + ":1", "signal": owner, "time": 1800000001, "entry_type": 1,
                "position_id": index, "volume": .01, "price": 4169, "profit": 10 + index,
                "net": 10 + index, "commission": 0, "swap": 0, "fee": 0}
        db.execute("INSERT INTO deals VALUES (?,?)", (deal["id"], json.dumps(deal)))
        db.execute("INSERT INTO events(time,level,message,signal,details) VALUES (1,'info',?,'',?)", ("connect " + owner, json.dumps({"account": owner})))
        db.execute("INSERT INTO events(time,level,message,signal,details) VALUES (1,'info',?,'','{}')", ("event " + owner,))
    db.execute("INSERT INTO messages VALUES (-1001,1,'message',NULL,'server:A',1)")
    db.commit()
    db.close()
    store = Store(path)
    try:
        assert path.with_name(path.name + ".pre-account-isolation.sqlite").exists()
        store.activate_account("server:B")
        assert store.get("settings")["fixed_lot"] == .07
        assert store.message(-1001, 1) is None
        assert all("server:B" in e["message"] for e in store.events())
        store.activate_account("server:A")
        assert store.get("settings")["fixed_lot"] == .03
        assert store.message(-1001, 1)["signal"] == "server:A"
    finally:
        store.close()
    a = build_report(path, "2020-01-01", "2030-01-01", account="server:A")
    b = build_report(path, "2020-01-01", "2030-01-01", account="server:B")
    assert a["net"] == 10 and b["net"] == 11
    assert a["count"] == b["count"] == 1
    assert {r["signal"] for r in a["rows"]} == {"server:A"}
    reopened = Store(path)
    reopened.close()  # Migration must be repeatable without losing any rows.
