import pytest

from signaldesk.broker import DemoBroker
from signaldesk.config import Settings
from signaldesk.engine import TradingEngine
from signaldesk.parser import parse_message
from signaldesk.store import Store


FX_TEMPLATE = "GOLD ❇️{side}❇️@ 📝 {start} - {end}\nTP1 🔼 {tp1}\nTP2 🔼 {tp2}\nTP3 🔼 {tp3}\n🥶\nSL 👀 {stop}\n📨#FXENGIN"


@pytest.mark.parametrize("start,end,stop,targets", [
    (4153, 4151, 4144, [4166, 4180, 4190]),
    (4142, 4140, 4133, [4155, 4160, 4170]),
    (4140, 4138, 4132, [4155, 4165, 4170]),
])
@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_exact_fx_template_keeps_entry_endpoints_stop_and_indexed_targets(side, start, end, stop, targets):
    raw = FX_TEMPLATE.format(side=side, start=start, end=end, stop=stop, tp1=targets[0], tp2=targets[1], tp3=targets[2])
    p = parse_message(raw)
    assert p.kind == "entry" and p.side == side.lower() and p.instrument == "XAU"
    assert (p.entry, p.entry_end, p.stop) == (str(start), str(end), str(stop))
    assert p.targets == targets


@pytest.mark.parametrize("raw,side,start,end,stop,targets", [
    ("Buy gold ranga 4190 / 4195\nTp 4200\nTp 4210\nTp 4220\nTp 4230\nTp 4240", "buy", "4190", "4195", None, [4200,4210,4220,4230,4240]),
    ("Sell gold range 4165\nTp 4155\nTp 4145\nTp 4136\nStop 🛑4180", "sell", "4165", None, "4180", [4155,4145,4136]),
    ("GOLD SELL@4165-4167 TP1:4155 TP2:4145 SL👀4180", "sell", "4165", "4167", "4180", [4155,4145]),
    ("شراء الذهب ٤١٥٣ — ٤١٥١\nTP1🔼٤١٦٦\nSL👀٤١٤٤", "buy", "4153", "4151", "4144", [4166]),
])
def test_screenshot_formats_and_compact_variations(raw, side, start, end, stop, targets):
    p = parse_message(raw)
    assert (p.kind, p.side, p.entry, p.entry_end, p.stop) == ("entry", side, start, end, stop)
    assert p.targets == targets


@pytest.mark.parametrize("raw", ["شراء ذهب 4153 و4151", "BUY GOLD 4153 4151", "BUY GOLD 4153-4151-4149"])
def test_only_explicit_two_endpoint_ranges_are_accepted(raw):
    assert parse_message(raw).kind == "ambiguous"


@pytest.mark.parametrize("raw", ["اشتري الذهب ✅", "بسم الله\nاشتري الذهب ✅", "عزز شراء ✅", "عزز بيع ✅"])
def test_short_announcements_wait_for_full_signal(raw):
    p = parse_message(raw)
    assert p.kind == "announcement" and p.entry is None and p.stop is None


@pytest.fixture
def desk(tmp_path):
    store = Store(tmp_path / "ranges.sqlite")
    broker = DemoBroker(store)
    broker.spread = 0
    cfg = Settings(channel_id=-1001, channel_name="A", targets_mode="channel", fixed_lot=.03)
    engine = TradingEngine(store, broker, cfg)
    engine.apply_settings(cfg)
    try:
        yield engine, broker, store
    finally:
        store.close()


@pytest.mark.parametrize("bid,expected", [(4150, "open"), (4153.8, "open"), (4154, "open"), (4154.01, "pending"), (4149.99, "pending")])
def test_whole_range_including_configured_margin_is_used(desk, bid, expected):
    engine, broker, _ = desk
    broker.set_price(bid)
    identifier = engine.receive(-1001, 1, FX_TEMPLATE.format(side="BUY", start=4153, end=4151, stop=4144, tp1=4166, tp2=4180, tp3=4190))
    s = engine.signals[identifier]
    assert (s["entry_low"], s["entry_high"], s["entry"]) == (4151,4153,4152)
    assert s["state"] == expected
    if expected == "open":
        assert s["sl"] == 4144 and s["tp"] == 4190 and s["fill"] == bid


def test_price_wait_restart_and_availability_keep_original_range(desk):
    engine, broker, store = desk
    broker.set_price(4156)
    root = engine.receive(-1001, 1, "BUY GOLD 4153-4151 SL4144 TP1:4166 TP2:4180 TP3:4190")
    assert engine.signals[root]["state"] == "pending"
    restored = TradingEngine(store, broker, engine.settings)
    broker.set_price(4153.8)
    restored.step()
    trade = restored.signals[root]
    assert trade["state"] == "open" and trade["sl"] == 4144
    broker.close(trade["position_id"], trade["volume"], "test")
    restored.reconcile()
    second = restored.receive(-1001, 2, "متاحة", reply=1)
    assert restored.signals[second]["root"] == root
    assert restored.signals[second]["state"] == "open"
    assert (restored.signals[second]["entry_low"], restored.signals[second]["entry_high"]) == (4151,4153)


def test_availability_recovers_targets_of_range_rejected_by_old_parser(desk):
    engine, broker, store = desk
    broker.set_price(4152)
    raw = "GOLD ❇️BUY❇️@ 📝 4153 - 4151\nTP1 🔼 4166\nTP2 🔼 4180\nTP3 🔼 4190\nSL 👀 4144"
    store.save_message(-1001, 1, raw, None, "", engine.clock())
    identifier = engine.receive(-1001, 2, "متاحة", reply=1)
    trade = engine.signals[identifier]
    assert trade["state"] == "open" and trade["sl"] == 4144
    assert (trade["entry_low"],trade["entry_high"],trade["channel_targets"],trade["tp"]) == (4151,4153,[4166,4180,4190],4190)


def test_sell_range_and_direct_range_keep_channel_stop(desk):
    engine, broker, _ = desk
    broker.set_price(4167.8)
    identifier = engine.receive(-1001, 1, "SELL GOLD 4165/4167\nTP1:4155 TP2:4145 TP3:4136\nStop🛑4180")
    assert engine.signals[identifier]["state"] == "open"
    assert engine.signals[identifier]["sl"] == 4180 and engine.signals[identifier]["tp"] == 4136
    engine.apply_settings(Settings(channel_id=-1001, channel_name="A", entry_mode="direct", targets_mode="channel"))
    broker.set_price(4200)
    identifier = engine.receive(-1001, 2, "BUY GOLD 4153-4151 SL4144 TP1:4210 TP2:4220 TP3:4230")
    assert engine.signals[identifier]["state"] == "open"
    assert engine.signals[identifier]["fill"] == 4200 and engine.signals[identifier]["sl"] == 4144


def test_simultaneous_channels_and_announcements_do_not_block_or_duplicate(desk):
    engine, broker, _ = desk
    engine.apply_settings(Settings(channel_id=-1001, watched_channels=[{"id": i, "name": str(i)} for i in (-1001,-1002,-1003)], targets_mode="channel"))
    broker.set_price(4141)
    raw = "BUY GOLD 4142-4140 SL4133 TP1:4155 TP2:4160 TP3:4170"
    for channel in (-1001,-1002,-1003):
        engine.receive(channel, 1, raw)
        engine.receive(channel, 2, "اشتري الذهب ✅")
        engine.receive(channel, 3, "عزز شراء ✅")
    assert len(broker.positions()) == 3
    assert len({s["id"] for s in engine.current_signals()}) == 3
    broker.set_price(4140)
    extra = engine.receive(-1002, 4, "BUY GOLD 4140-4138 SL4132 TP1:4155 TP2:4165 TP3:4170")
    assert engine.signals[extra]["state"] == "open" and len(broker.positions()) == 4


def test_full_template_edit_updates_pending_range_targets_and_keeps_open_fill(desk):
    engine, broker, _ = desk
    engine.settings.follow_edits = True
    engine.apply_settings(engine.settings)
    broker.set_price(4160)
    identifier = engine.receive(-1001, 1, "BUY GOLD 4153-4151 SL4144 TP1:4166")
    engine.receive(-1001, 1, "BUY GOLD 4158-4159 SL4145 TP1:4167 TP2:4170", edited=True)
    s = engine.signals[identifier]
    assert (s["entry_low"],s["entry_high"],s["channel_targets"]) == (4158,4159,[4167,4170])
    engine.step()
    assert s["state"] == "open" and s["fill"] == 4160
    engine.receive(-1001, 1, "BUY GOLD 4148-4150 SL4146 TP1:4175", edited=True)
    assert s["fill"] == 4160 and s["entry_low"] == 4158
    assert s["sl"] == 4146 and s["channel_targets"] == [4175]
