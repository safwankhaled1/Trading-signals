import pytest

from signaldesk.broker import DemoBroker
from signaldesk.config import Settings
from signaldesk.engine import TradingEngine
from signaldesk.parser import parse_message
from signaldesk.service import Service
from signaldesk.store import Store


@pytest.fixture
def trading(tmp_path):
    store = Store(tmp_path / "flexible.sqlite")
    broker = DemoBroker(store)
    broker.spread = 0
    broker.set_price(4160)
    clock = [1_800_000_000.0]
    engine = TradingEngine(store, broker, Settings(), clock=lambda: clock[0])
    yield engine, broker, store, clock
    store.close()


@pytest.mark.parametrize("text,side", [
    ("كرر شراء الان من 60\n\nستوب 59.5", "buy"),
    ("كرر شراء الآن من 61\nستوب 60.5", "buy"),
    ("شرا دهب حالياً عند 60\nستبو59,5", "buy"),
    ("اشري الذهب ٦٠\nستوب ٥٩٫٥", "buy"),
    ("شراء\u200f الآن من\u200b60\nستووب 59.5", "buy"),
    ("شراءالانمن60 ستوب59.5", "buy"),
    ("ستوب: 59.5\nشراء الان من 60", "buy"),
    ("دخول شراء عند ۶۰\nوقف الخسارة ۵۹٫۵", "buy"),
    ("متاحة للشراء من 60 ستوب 59.5", "buy"),
    ("بييع الذهب الان 60\nستوب 60.5", "sell"),
    ("SELL 4160 SL4160.5", "sell"),
    ("BUY BTCUSD 84905 stop 84890", "buy"),
])
def test_human_writing_variations(text, side):
    parsed = parse_message(text)
    assert parsed.kind in {"entry", "repeat"}
    assert parsed.side == side and parsed.entry is not None and parsed.stop is not None


@pytest.mark.parametrize("text", [
    "شراء بيع من 60 ستوب 59.5", "شراء من 60 او 61 ستوب 59.5",
    "شراء 60 ستوب 59.5 ستوب 58.5", "شراء ذهب BTCUSD 4160",
    "شراء الان ستوب 59.5", "شراء 60 ستوب غلط",
])
def test_ambiguous_messages_do_not_trade(trading, text):
    engine, broker, store, _ = trading
    assert parse_message(text).kind == "ambiguous"
    engine.receive(-1001, 1, text)
    assert broker.positions() == [] and engine.signals == {}
    assert any(e["level"] == "warning" for e in store.events())


@pytest.mark.parametrize("text", ["لا شراء ذهب من 60", "تم شراء الذهب 4160", "XAUUSD +100 pips ✅", "شراء 60 +100 نقطة ✅"])
def test_reports_and_cancelled_commands_never_trade(trading, text):
    engine, broker, _, _ = trading
    engine.receive(-1001, 1, text)
    assert not broker.positions()


def test_all_screenshot_messages_and_legacy_ignored_reply(trading):
    engine, broker, store, _ = trading
    # A legacy parser accidentally linked ignored entry 64 to the earlier entry 66.
    broker.set_price(4166)
    previous_id = engine.receive(-1001, 1, "شراء ذهب من 66 ستوب 65.5")
    broker.close(engine.signals[previous_id]["ticket"], .03, "manual")
    engine.reconcile()
    store.save_message(-1001, 2, "كرر شراء ذهب الان 64\nستوب 63.5", None, previous_id, engine.clock())
    broker.set_price(4164)
    repeat_id = engine.receive(-1001, 3, "متاحة", reply=2)
    assert engine.signals[repeat_id]["entry"] == 4164
    assert engine.signals[repeat_id]["sl"] == 4163.5
    broker.set_price(4160)  # This closes entry 64 on its broker SL.
    second_id = engine.receive(-1001, 4, "كرر شراء الان من 60\nستوب 59.5")
    assert engine.signals[second_id]["state"] == "open"
    assert engine.signals[second_id]["entry"] == 4160
    assert engine.signals[second_id]["sl"] == 4159.5


def test_latest_entry_text_not_unrelated_previous_fill(trading):
    engine, broker, store, _ = trading
    store.save_message(-1001, 1, "كرر شراء الان من 60\nستوب 59.5", None, "", engine.clock())
    identifier = engine.receive(-1001, 2, "متاحه مجددا 🔥")
    assert engine.signals[identifier]["state"] == "open"
    assert engine.signals[identifier]["entry"] == 4160


def test_explicit_asset_never_silently_uses_selected_gold(trading):
    engine, broker, _, _ = trading
    engine.receive(-1001, 1, "شراء EURUSD 1.2345 SL1.2335")
    engine.receive(-1001, 2, "شراء فضة 60 ستوب 59.5")
    assert not broker.positions()


def test_turning_on_management_applies_to_already_monitored_manual_trade(trading):
    engine, broker, _, _ = trading
    engine.settings.management_scope = "all"
    p = broker.open("buy", .03, 0, "manual")
    engine.step()
    manual = next(s for s in engine.signals.values() if s.get("manual"))
    assert manual["targets"] == [] and broker.positions()[0]["tp"] == 0
    cfg = Settings(management_scope="all", manage_manual_stops=True)
    engine.apply_settings(cfg)
    engine.step()
    assert broker.positions()[0]["sl"] == 4159.5
    assert broker.positions()[0]["tp"] == 4175
    broker.set_price(4165)
    engine.step()
    assert manual["volume"] == .02 and manual["secured"]
    assert broker.positions()[0]["sl"] == 4160
    assert broker.positions()[0]["tp"] == 4175
    # Restart must keep the completed partial close and protected stop.
    recovered = TradingEngine(engine.store, broker, cfg, clock=engine.clock)
    recovered.step()
    assert recovered.signals[manual["id"]]["volume"] == .02
    assert len(broker.deals()) == 1
    assert broker.positions()[0]["ticket"] == p["ticket"]


def test_manual_sell_uses_same_targets_and_stops_and_can_be_disabled(trading):
    engine, broker, _, _ = trading
    cfg = Settings(management_scope="all", manage_manual_stops=True)
    engine.apply_settings(cfg)
    broker.open("sell", .03, 0, "manual")
    engine.step()
    assert broker.positions()[0]["sl"] == 4160.5
    assert broker.positions()[0]["tp"] == 4145
    cfg.manage_manual_stops = False
    engine.apply_settings(cfg)
    broker.set_price(4155)
    engine.step()
    assert broker.positions()[0]["volume"] == .03


def test_manual_channel_targets_when_no_channel_trade_exists(trading):
    engine, broker, _, _ = trading
    engine.apply_settings(Settings(management_scope="all", manage_manual_stops=True, targets_mode="channel"))
    broker.open("buy", .03, 0, "manual")
    engine.step()
    engine.receive(-1001, 1, "اهدافنا\n4165\n4170\n4175")
    engine.step()
    assert broker.positions()[0]["tp"] == 4175


def test_large_archive_does_not_make_a_full_history_scan_in_trading_step(trading, monkeypatch):
    engine, broker, _, clock = trading
    for i in range(60):
        engine.signals[str(i)] = {"id": str(i), "account": broker.identity, "symbol": broker.symbol,
            "state": "closed", "position_id": i + 1, "channel": 0, "channel_name": "archive", "manual": False}
    calls = []
    monkeypatch.setattr(broker, "deals", lambda pid: calls.append(pid) or [])
    engine.step()
    assert len(calls) == 3
    for _ in range(19):
        clock[0] += 5
        engine.step()
    assert len(set(calls)) == 60  # Bounded work still eventually refreshes every trade.


def test_telegram_callback_executes_before_any_ui_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNALDESK_DATA_DIR", str(tmp_path))
    service = Service("demo")
    try:
        service.connect_broker()
        service.broker.spread = 0
        service.broker.set_price(4160)
        monkeypatch.setattr(service, "snapshot", lambda: pytest.fail("Rendering blocked execution"))
        service.on_message(-1001, 1, "كرر شراء الان من 60\nستوب 59.5", None, False, service.engine.clock(), "اختبار")
        assert len(service.broker.positions()) == 1
        trade = next(iter(service.engine.signals.values()))
        assert trade["entry_request_delay_ms"] < 200
    finally:
        service.store.close()
        service.pool.shutdown()
