import pytest

from signaldesk.broker import DemoBroker
from signaldesk.config import Settings
from signaldesk.engine import TradingEngine
from signaldesk.store import Store


@pytest.fixture
def desk(tmp_path):
    store = Store(tmp_path / "direct.sqlite")
    broker = DemoBroker(store)
    broker.spread = .2
    broker.set_price(4117.1)
    now = [1800000000.]
    engine = TradingEngine(store, broker, Settings(entry_mode="direct", fixed_lot=.04), clock=lambda: now[0])
    yield engine, broker, store, now
    store.close()


def test_last_failed_signal_executes_at_market_with_exact_channel_stop(desk):
    engine, broker, _, _ = desk
    sid = engine.receive(-1001, 1, "اشتري ذهب الان من 15\n\nستوب 14.5")
    trade = engine.signals[sid]
    assert trade["state"] == "open" and trade["fill"] == 4117.3
    assert trade["entry"] == 4115 and trade["sl"] == 4114.5
    assert trade["volume"] == .04 and len(broker.positions()) == 1


@pytest.mark.parametrize("side,bid,text,stop", [
    ("buy", 4159.8, "شراء 4115 ستوب 4114.5", 4114.5),
    ("sell", 4070, "بيع 4115 ستوب 4115.5", 4115.5),
])
def test_direct_entry_ignores_large_price_distance_and_fixed_stop_option(desk, side, bid, text, stop):
    engine, broker, _, _ = desk
    engine.settings.stop_mode = "fixed"
    engine.settings.stop_distance = .5
    broker.set_price(bid)
    sid = engine.receive(-1001, 1, text)
    trade = engine.signals[sid]
    assert trade["state"] == "open" and trade["side"] == side
    assert trade["fill"] == broker.tick()["ask" if side == "buy" else "bid"]
    assert trade["sl"] == stop


def test_short_prices_do_not_reintroduce_the_distance_limit_in_direct_mode(desk):
    engine, broker, _, _ = desk
    broker.set_price(4159.8)
    sid = engine.receive(-1001, 1, "شراء ذهب من 15 ستوب 14.5")
    assert engine.signals[sid]["entry"] == 4115
    assert engine.signals[sid]["sl"] == 4114.5 and engine.signals[sid]["state"] == "open"


@pytest.mark.parametrize("text,reason", [
    ("شراء ذهب من 15", "لا تحتوي ستوب"),
    ("شراء 4115 ستوب 4118", "غير صالح"),
    ("بيع 4115 ستوب 4117.2", "غير صالح"),
])
def test_direct_missing_or_invalid_channel_stop_rejects_without_fallback_or_later_entry(desk, text, reason):
    engine, broker, store, _ = desk
    sid = engine.receive(-1001, 1, text)
    trade = engine.signals[sid]
    assert trade["state"] == "rejected" and reason in trade["last_error"]
    assert not broker.positions() and store.action("entry:" + sid) is None
    broker.set_price(4130)
    engine.step(include_history=False)
    assert trade["state"] == "rejected" and not broker.positions()


def test_direct_respects_broker_minimum_stop_distance(desk):
    engine, broker, _, _ = desk
    broker.info["trade_stops_level"] = 500
    sid = engine.receive(-1001, 1, "شراء 4115 ستوب 4114.5")
    assert engine.signals[sid]["state"] == "rejected" and not broker.positions()


def test_new_direct_entry_without_stop_does_not_borrow_another_signals_stop(desk):
    engine, broker, _, _ = desk
    engine.receive(-1001, 1, "شراء 4115 ستوب 4114.5")
    sid = engine.receive(-1001, 2, "شراء 4115")
    assert engine.signals[sid]["state"] == "rejected"
    assert engine.signals[sid]["signal_stop"] is None and len(broker.positions()) == 1


def test_direct_ambiguous_short_price_is_not_guessed(desk):
    engine, broker, store, _ = desk
    broker.set_price(4164.8)  # ASK 4165 is equally close to 4115 and 4215.
    assert not engine.receive(-1001, 1, "شراء من 15 ستوب 14.5")
    assert not broker.positions()
    assert any("ملتبس" in e["message"] for e in store.events())


def test_direct_duplicate_repeat_and_availability_preserve_original_stop_after_price_shift(desk):
    engine, broker, _, _ = desk
    sid = engine.receive(-1001, 1, "شراء ذهب من 15 ستوب 14.5")
    engine.receive(-1001, 2, "متاحة", reply=1)
    assert len(broker.positions()) == 1
    trade = engine.signals[sid]
    broker.close(trade["ticket"], trade["volume"], "manual")
    engine.step(include_history=False)
    broker.set_price(4217.1)
    new_sid = engine.receive(-1001, 3, "متاحة", reply=1)
    again = engine.signals[new_sid]
    assert again["state"] == "open" and again["entry"] == 4115 and again["fill"] == 4217.3
    assert again["sl"] == 4114.5 and again["root"] == trade["root"]
    engine.receive(-1001, 3, "متاحة", reply=1)
    assert len(broker.positions()) == 1


def test_direct_pause_and_expiry_still_apply_and_restart_does_not_duplicate(desk):
    engine, broker, store, now = desk
    engine.paused = True
    sid = engine.receive(-1001, 1, "شراء 4115 ستوب 4114.5")
    assert engine.signals[sid]["state"] == "pending" and not broker.positions()
    now[0] += 600
    engine.paused = False
    engine.step(include_history=False)
    assert engine.signals[sid]["state"] == "expired" and not broker.positions()
    engine.receive(-1001, 1, "شراء 4115 ستوب 4114.5")
    assert not broker.positions()
    new_sid = engine.receive(-1001, 2, "شراء 4115 ستوب 4114.5")
    assert engine.signals[new_sid]["state"] == "open"
    restored = TradingEngine(store, broker, engine.settings, clock=lambda: now[0])
    restored.step(include_history=False)
    restored.receive(-1001, 2, "شراء 4115 ستوب 4114.5")
    assert len(broker.positions()) == 1 and restored.signals[new_sid]["sl"] == 4114.5


def test_direct_uses_channel_profile_and_keeps_existing_pending_rules(desk):
    engine, broker, _, _ = desk
    engine.apply_settings(Settings(channel_id=-1001))
    sid = engine.receive(-1001, 1, "شراء 4115 ستوب 4114.5")
    assert engine.signals[sid]["state"] == "pending"
    engine.apply_settings(Settings(channel_id=-1001, entry_mode="direct"))
    engine.step(include_history=False)
    assert engine.signals[sid]["state"] == "pending"
    direct_sid = engine.receive(-1001, 2, "شراء 4115 ستوب 4114.5")
    assert engine.signals[direct_sid]["state"] == "open"
    engine.apply_settings(Settings(channel_id=-1002))
    assert engine.settings_for(-1001).entry_mode == "direct"
    assert engine.settings_for(-1002).entry_mode == "range"


def test_old_settings_default_to_range_and_unknown_mode_is_rejected():
    assert Settings.from_dict({"fixed_lot": .04}).entry_mode == "range"
    with pytest.raises(ValueError, match="entry_mode"):
        Settings.from_dict({"entry_mode": "unknown"})
