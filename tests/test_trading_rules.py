import math

import pytest

from signaldesk.broker import BrokerError, DemoBroker, UncertainExecution, quantize_volume
from signaldesk.config import Settings
from signaldesk.engine import TradingEngine
from signaldesk.parser import expand_price, parse_message
from signaldesk.reports import build_report, export_report
from signaldesk.store import Store


@pytest.fixture
def desk(tmp_path):
    store = Store(tmp_path / "demo.sqlite")
    broker = DemoBroker(store)
    broker.spread = 0
    now = [1_800_000_000.0]
    engine = TradingEngine(store, broker, Settings(), clock=lambda: now[0])
    yield engine, broker, store, now
    store.close()


def send(engine, mid=1, text="اشتري ذهب الآن من 4\nستوب 3.5", reply=None, edited=False):
    identifier = engine.receive(-1001, mid, text, reply, edited, channel_name="اختبار")
    return engine.signals.get(identifier)


def test_short_price_and_half_dollar_stop(desk):
    engine, broker, _, _ = desk
    s = send(engine)
    assert s["entry"] == 4154
    assert s["state"] == "open"
    assert s["sl"] == 4153.5
    assert s["volume"] == .03


def test_gold_message_never_trades_on_selected_forex_symbol(desk):
    engine, broker, store, _ = desk
    broker.symbol = "EURUSD"
    assert send(engine) is None
    assert broker.positions() == []
    assert any("لن تُنفّذ على الرمز المختار" in e["message"] for e in store.events())


@pytest.mark.parametrize("text", ["اشتري btcusd  84905", "اشتري btcusd \n84905", "اشتري بيتكوين من 84905", "BUY BTCUSD 84905 SL 84890", "شراء BTCUSD.m من 84905"])
def test_bitcoin_entry_formats(desk, text):
    engine, broker, _, _ = desk
    broker.symbol = "BTCUSD.m"
    broker.bid = 84905
    s = send(engine, text=text)
    assert s["state"] == "open"
    assert s["entry"] == 84905
    assert s["symbol"] == "BTCUSD.m"


def test_bitcoin_signal_never_executes_on_gold(desk):
    engine, broker, store, _ = desk
    assert send(engine, text="اشتري btcusd 84905") is None
    assert broker.positions() == []
    assert any("لرمز مختلف" in e["message"] for e in store.events())


def test_bitcoin_respects_margin_and_explains_spread_stop(desk):
    engine, broker, store, _ = desk
    broker.symbol = "BTCUSD"
    broker.bid, broker.spread = 84905, 20
    s = send(engine, text="اشتري btcusd 84905")
    assert s["state"] == "pending" and "ASK" in s["last_error"]
    s = send(engine, mid=2, text="اشتري btcusd 84925")
    assert s["state"] == "pending" and "السبريد" in s["last_error"]
    assert not broker.positions()
    engine.settings.stop_distance = 30
    s = send(engine, mid=3, text="اشتري btcusd 84925")
    assert s["state"] == "open"


def test_switch_symbol_keeps_old_closed_signal_out_of_bitcoin_repeat(desk):
    engine, broker, _, _ = desk
    gold = send(engine)
    broker.close(gold["ticket"], gold["volume"], "manual")
    engine.reconcile()
    broker.symbol, broker.bid = "BTCUSD", 84905
    assert engine.current_signals() == []
    assert send(engine, mid=2, text="متاحة") is None
    bitcoin = send(engine, mid=3, text="اشتري btcusd 84905")
    assert bitcoin["state"] == "open"
    assert bitcoin["root"] != gold["root"]


@pytest.mark.parametrize("price", [4149, 4150, 4151])
def test_inclusive_margin(desk, price):
    engine, broker, _, _ = desk
    broker.set_price(price)
    assert send(engine, text="اشتري ذهب من 4150 ستوب 4148.5")["state"] == "open"


def test_wait_then_execute_with_frozen_entry(desk):
    engine, broker, _, now = desk
    s = send(engine, text="اشتري ذهب من 4150 ستوب 4149.5")
    assert s["state"] == "pending"
    broker.set_price(4151)
    now[0] += 60
    engine.step()
    assert s["state"] == "open"
    assert s["entry"] == 4150
    assert s["fill"] == 4151


def test_expiry_and_disabled_expiry(desk):
    engine, broker, _, now = desk
    s = send(engine, text="بيع ذهب من 4150 ستوب 4150.5")
    now[0] += 601
    broker.set_price(4150)
    engine.step()
    assert s["state"] == "expired"
    engine.settings.expiry_enabled = False
    broker.set_price(4160)
    other = send(engine, 2, "بيع ذهب من 4150 ستوب 4150.5")
    now[0] += 999999
    engine.step()
    assert other["state"] == "pending"


def test_duplicate_message_never_opens_twice(desk):
    engine, broker, _, _ = desk
    send(engine)
    send(engine)
    assert len(broker.positions()) == 1


def test_available_only_after_closed_and_new_message(desk):
    engine, broker, _, _ = desk
    first = send(engine)
    send(engine, 2, "متاحة", 1)
    assert len(broker.positions()) == 1
    broker.close(first["ticket"], .03, "manual")
    engine.step()
    assert first["state"] == "closed"
    engine.step()
    assert not broker.positions()  # a skipped message is not deferred
    second = send(engine, 3, "متاحة", 1)
    assert second["state"] == "open"
    assert second["root"] == first["root"]
    assert len(broker.positions()) == 1


def test_repeat_different_price_is_independent(desk):
    engine, broker, _, _ = desk
    first = send(engine)
    broker.set_price(4155)
    second = send(engine, 2, "كرر شراء ذهب الآن من 5 ستوب 4.5")
    assert second["state"] == "open"
    assert second["root"] != first["root"]
    assert len(broker.positions()) == 2


def test_repeat_same_price_is_blocked(desk):
    engine, broker, _, _ = desk
    send(engine)
    send(engine, 2, "كرر شراء ذهب من 4 ستوب 3.5")
    assert len(broker.positions()) == 1


def test_separate_signal_same_price_allowed(desk):
    engine, broker, _, _ = desk
    send(engine)
    send(engine, 2)
    assert len(broker.positions()) == 2


def test_market_entry_invalid_original_stop_uses_fallback(desk):
    engine, broker, _, _ = desk
    broker.set_price(4153)
    s = send(engine)
    # The nearest abbreviated entry is 4154, within the one-dollar margin.
    assert s["state"] == "open"
    assert s["sl"] == 4152.5


def test_partial_close_and_breakeven_once(desk):
    engine, broker, _, _ = desk
    s = send(engine)
    broker.set_price(4159)
    engine.step()
    assert s["volume"] == .02
    assert s["sl"] == 4154
    assert s["secured"] is True
    engine.step()
    assert s["volume"] == .02
    assert len(broker.deals()) == 1
    broker.set_price(4164)
    engine.step()
    assert s["volume"] == .01
    broker.set_price(4169)
    engine.step()
    engine.step()
    assert s["state"] == "closed"
    assert s["realized"] == 30.0


@pytest.mark.parametrize("side", ["buy", "sell"])
def test_bitcoin_native_tp_and_partial_targets_with_spread(desk, side):
    engine, broker, store, now = desk
    broker.symbol, broker.spread = "BTCUSD", 10
    broker.bid = 84832.5 if side == "buy" else 84842.5
    engine.settings.stop_distance = 100
    s = send(engine, text=("اشتري" if side == "buy" else "بيع") + " btcusd 84842.5")
    direction = 1 if side == "buy" else -1
    final = 84842.5 + direction * 15
    assert broker.positions()[0]["tp"] == final
    # ASK reaching a buy goal (or BID reaching a sell goal) is insufficient.
    broker.set_price(84842.5 if side == "buy" else 84837.5)
    engine.step()
    assert s["volume"] == .03 and not s["completed"]
    for index, distance in enumerate((5, 10)):
        price = 84842.5 + direction * distance
        broker.set_price(price if side == "buy" else price - broker.spread)
        engine.step()
        assert s["volume"] == pytest.approx(.02 - index * .01)
        assert s["secured"] and s["sl"] == 84842.5
        assert broker.positions()[0]["tp"] == final
    broker.set_price(final if side == "buy" else final - broker.spread)
    assert not broker.positions()  # Broker TP works even before the engine's next step.
    recovered = TradingEngine(store, broker, engine.settings, clock=lambda: now[0])
    recovered.step()
    restored = recovered.signals[s["id"]]
    assert restored["state"] == "closed"
    assert restored["completed"] == ["stage:0", "stage:1", "stage:2"]
    assert len(broker.deals()) == 3
    recovered.step()
    assert len(broker.deals()) == 3


def test_channel_tp_updates_after_targets_and_preserves_secured_stop(desk):
    engine, broker, _, _ = desk
    engine.settings.targets_mode = "channel"
    s = send(engine)
    assert broker.positions()[0]["tp"] == 0
    send(engine, 2, "اهداف 4160 4170 4180", reply=1)
    engine.step()
    assert broker.positions()[0]["tp"] == 4180
    broker.set_price(4160)
    engine.step()
    assert s["secured"] and broker.positions()[0]["sl"] == s["fill"]
    send(engine, 3, "اهداف 4160 4175 4190", reply=1)
    engine.step()
    assert broker.positions()[0]["tp"] == 4190
    assert broker.positions()[0]["sl"] == s["fill"]


def test_disabled_native_tp_still_manages_partial_targets(desk):
    engine, broker, _, _ = desk
    engine.settings.native_tp_enabled = False
    s = send(engine)
    assert broker.positions()[0]["tp"] == 0
    broker.set_price(4159)
    engine.step()
    assert s["volume"] == .02 and s["secured"]
    assert broker.positions()[0]["tp"] == 0


def test_tp_rejection_retries_without_reopening_and_targets_still_work(desk):
    engine, broker, _, now = desk
    original = broker.modify
    attempts = []

    def reject_target(ticket, stop=None, target=None):
        if target is not None:
            attempts.append(target)
            raise BrokerError("Invalid TP")
        return original(ticket, stop, target)

    broker.modify = reject_target
    s = send(engine)
    assert s["state"] == "open" and s["tp_error"] == "Invalid TP"
    for _ in range(3):
        engine.step()
    assert len(attempts) == 1 and len(broker.positions()) == 1
    broker.set_price(4159)
    engine.step()
    assert s["volume"] == .02 and s["secured"]
    broker.modify = original
    now[0] += 5
    engine.step()
    assert broker.positions()[0]["tp"] == 4169
    assert not s["tp_error"] and len(broker.positions()) == 1


def test_monitoring_manual_trade_preserves_its_existing_tp(desk):
    engine, broker, _, _ = desk
    engine.settings.management_scope = "all"
    manual = broker.open("buy", .03, 4153, "manual")
    broker.modify(manual["ticket"], target=4180)
    engine.step()
    s = list(engine.signals.values())[0]
    assert s["manual"] and not s["targets"]
    assert broker.positions()[0]["tp"] == 4180


def test_legacy_open_trade_gets_tp_on_restart_without_repeating_close(desk):
    engine, broker, store, now = desk
    engine.settings.native_tp_enabled = False
    s = send(engine)
    broker.set_price(4159)
    engine.step()
    s["config"].pop("native_tp_enabled")  # Previously saved versions have no TP option.
    engine.save(s)
    recovered = TradingEngine(store, broker, clock=lambda: now[0])
    recovered.step()
    assert broker.positions()[0]["tp"] == 4169
    assert broker.positions()[0]["sl"] == 4154
    assert recovered.signals[s["id"]]["volume"] == .02
    assert len(broker.deals()) == 1


def test_native_tp_execution_slippage_still_records_final_goal(desk):
    engine, broker, _, _ = desk
    s = send(engine)
    original = broker.close

    def slipped_close(ticket, volume, comment):
        result = original(ticket, volume, comment)
        if comment == "broker-target":
            broker.history[-1]["price"] -= .5
        return result

    broker.close = slipped_close
    broker.set_price(4169)
    engine.step()
    assert s["state"] == "closed"
    assert s["completed"] == ["stage:2"]
    assert len(broker.deals()) == 1


def test_sell_targets_use_ask_not_bid(desk):
    engine, broker, _, _ = desk
    broker.spread = .2
    s = send(engine, text="بيع ذهب من 4154 ستوب 4154.5")
    broker.set_price(4149)
    engine.step()
    assert not s["completed"]
    broker.set_price(4148.8)
    engine.step()
    assert s["volume"] == .02


def test_channel_targets_reply_chain_and_last_fallback(desk):
    engine, broker, _, _ = desk
    engine.settings.targets_mode = "channel"
    first = send(engine)
    send(engine, 2, "+ 50 نقطة ✅", 1)
    send(engine, 3, "اهدافنا\n4160\n4170\n4180", 2)
    assert [t["price"] for t in first["targets"]] == [4160, 4170, 4180]
    second = send(engine, 4)
    send(engine, 5, "اهدافنا\n4161\n4171\n4181")
    assert second["targets"][0]["price"] == 4161
    assert first["targets"][0]["price"] == 4160


def test_manual_targets_ignore_channel_targets(desk):
    engine, _, _, _ = desk
    s = send(engine)
    send(engine, 2, "اهدافنا\n4190\n4200", 1)
    assert s["targets"][0]["price"] == 4159


def test_channel_management_optional(desk):
    engine, broker, _, _ = desk
    s = send(engine)
    broker.set_price(4156)
    send(engine, 2, "نحجز ربح ونستمر", 1)
    assert not s["secured"]
    engine.settings.channel_management = True
    send(engine, 3, "نحجز ربح ونستمر", 1)
    assert s["secured"]


def test_edits_disabled_and_enabled(desk):
    engine, broker, _, _ = desk
    s = send(engine)
    send(engine, text="اشتري ذهب من 4 ستوب 3", edited=True)
    assert s["sl"] == 4153.5
    engine.settings.follow_edits = True
    send(engine, text="اشتري ذهب من 5 ستوب 3", edited=True)
    assert s["sl"] == 4153
    assert s["fill"] == 4154
    assert len(broker.positions()) == 1


def test_recovery_does_not_backfill_missed_target(desk):
    engine, broker, store, now = desk
    s = send(engine)
    broker.set_price(4159)
    broker.set_price(4156)  # no engine step while the GUI/device was unavailable
    recovered = TradingEngine(store, broker, engine.settings, clock=lambda:now[0])
    recovered.step()
    restored = recovered.signals[s["id"]]
    assert restored["volume"] == .03
    assert not restored["completed"]
    broker.set_price(4159)
    recovered.step()
    assert restored["volume"] == .02


def test_recovery_does_not_repeat_completed_partial(desk):
    engine, broker, store, now = desk
    s = send(engine)
    broker.set_price(4159)
    engine.step()
    recovered = TradingEngine(store, broker, engine.settings, clock=lambda:now[0])
    recovered.step()
    assert recovered.signals[s["id"]]["volume"] == .02
    assert len(broker.deals()) == 1


def test_uncertain_entry_never_retries(desk):
    engine, broker, _, _ = desk
    calls = []
    def uncertain(*args):
        calls.append(args)
        raise UncertainExecution("timeout")
    broker.open = uncertain
    s = send(engine)
    for _ in range(5):
        engine.step()
    assert s["state"] == "uncertain"
    assert len(calls) == 1


def test_crash_after_broker_fill_recovers_using_comment(desk):
    engine, broker, store, now = desk
    original = broker.open
    def filled_without_ack(*args):
        original(*args)
        raise UncertainExecution("timeout")
    broker.open = filled_without_ack
    s = send(engine)
    recovered = TradingEngine(store, broker, engine.settings, clock=lambda:now[0])
    recovered.step()
    assert recovered.signals[s["id"]]["state"] == "open"
    assert len(broker.positions()) == 1


def test_pause_keeps_management_running(desk):
    engine, broker, _, _ = desk
    s = send(engine)
    engine.paused = True
    other = send(engine, 2)
    assert other["state"] == "pending"
    broker.set_price(4159)
    engine.step()
    assert s["volume"] == .02


def test_risk_volume_respects_account_currency_and_step(desk):
    engine, broker, _, _ = desk
    engine.settings.size_mode = "risk"
    engine.settings.risk_percent = .1
    s = send(engine)
    assert s["volume"] == .2  # $10 / $50 per lot at a $0.50 stop


def test_reports_group_partials_as_one_trade_and_export(desk, tmp_path):
    engine, broker, store, _ = desk
    send(engine)
    for price in (4159, 4164, 4169):
        broker.set_price(price)
        engine.step()
        engine.sync_history()
    engine.step()
    report = build_report(store.path, "2020-01-01", "2030-01-01")
    assert report["count"] == 1
    assert report["closed_count"] == 1
    assert report["win_rate"] == 100
    assert report["net"] == 30
    export_report(report, tmp_path / "report.xlsx")
    assert (tmp_path / "report.xlsx").exists()


def test_parser_ignores_news_and_profit_reports():
    for text in ["XAUUSD +100 pips ✅", "صدر الآن: أمريكا معدل البطالة 4.2%", "حصاد الأسبوع XAUUSD + 300", "صباح الخير"]:
        assert parse_message(text).kind == "ignored"
    assert parse_message("اشتري دهب الان من ٤ ستوب ٣٫٥").entry == "4"


def test_price_ambiguity_does_not_guess():
    with pytest.raises(ValueError):
        expand_price("4", 4199)


def test_invalid_lot_not_rounded_up():
    with pytest.raises(BrokerError):
        quantize_volume(.005, .01, 100, .01)


def test_no_history_no_false_closed(desk):
    engine, broker, _, _ = desk
    s = send(engine)
    broker.positions = lambda: []
    broker.deals = lambda *args: []
    engine.reconcile()
    assert s["state"] == "open"


def test_selected_manual_scope_preserves_unselected(desk):
    engine, broker, _, _ = desk
    manual = broker.open("buy", .03, 4153, "manual")
    engine.step()
    assert not engine.signals
    engine.settings.management_scope = "selected"
    engine.settings.selected_tickets = [manual["ticket"]]
    engine.step()
    adopted = list(engine.signals.values())[0]
    assert adopted["manual"]
    assert adopted["targets"] == []
    assert broker.positions()[0]["sl"] == 4153


def test_broker_partial_fill_completes_only_remaining_target_volume(desk):
    engine, broker, _, _ = desk
    engine.settings.fixed_lot = .04
    engine.settings.stages = [{"pips": 50., "lot": .02}, {"pips": 100., "lot": .02}]
    s = send(engine)
    original_close = broker.close
    calls = []
    def partially_filled(ticket, volume, comment):
        calls.append(volume)
        return original_close(ticket, min(.01, volume), comment)
    broker.close = partially_filled
    broker.set_price(4159)
    engine.step()
    assert s["volume"] == .03
    assert not s["completed"]
    assert s["secured"]
    engine.step()
    assert s["volume"] == .02
    assert s["completed"] == ["stage:0"]
    engine.step()
    assert calls == [.02, .01]


def test_expiry_runs_even_when_quotes_unavailable(desk):
    engine, broker, _, now = desk
    s = send(engine, text="بيع ذهب من 4150 ستوب 4150.5")
    now[0] += 601
    def unavailable():
        raise BrokerError("Disconnected")
    broker.tick = unavailable
    with pytest.raises(BrokerError):
        engine.step()
    assert s["state"] == "expired"


def test_manual_closure_is_reconciled_even_without_fresh_quotes(desk):
    engine, broker, _, _ = desk
    s = send(engine)
    broker.close(s["ticket"], s["volume"], "manual")
    def stale_quote():
        raise BrokerError("سعر الرمز قديم")
    broker.tick = stale_quote
    with pytest.raises(BrokerError, match="قديم"):
        engine.step()
    assert s["state"] == "closed" and s["volume"] == 0
    assert not s["completed"]  # A manual exit is never reported as a TP hit.


def test_manual_partial_history_updates_while_quote_is_stale(desk):
    engine, broker, store, _ = desk
    s = send(engine)
    broker.bid = s["fill"] + 2
    broker.close(s["ticket"], .01, "manual")
    def stale_quote():
        raise BrokerError("سعر الرمز قديم")
    broker.tick = stale_quote
    with pytest.raises(BrokerError, match="قديم"):
        engine.step()
    assert s["state"] == "open" and s["volume"] == .02
    assert s["realized"] == 2 and len(store.deals()) == 1
    assert not s["completed"] and not s["secured"]
