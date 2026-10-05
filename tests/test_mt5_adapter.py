from types import SimpleNamespace

import pytest

from signaldesk.broker import AccountChanged, BrokerError, MT5Broker, UncertainExecution, valid_stop, symbol_catalog


def broker_with_api(api):
    broker = MT5Broker.__new__(MT5Broker)
    broker.mt5 = api
    broker.info = {"filling_mode": 1}
    broker.account = lambda:{"trade_allowed":True,"hedging":True}
    return broker


def api_constants():
    return {"ORDER_FILLING_FOK":0,"ORDER_FILLING_IOC":1,"ORDER_FILLING_RETURN":2,
            "TRADE_RETCODE_TIMEOUT":10012,"TRADE_RETCODE_CONNECTION":10031,
            "TRADE_RETCODE_DONE":10009,"TRADE_RETCODE_DONE_PARTIAL":10010,"TRADE_RETCODE_NO_CHANGES":10025}


def test_order_check_rejection_never_sends():
    sent = []
    api = SimpleNamespace(**api_constants(), order_check=lambda req:SimpleNamespace(retcode=10016,comment="Invalid stops"),
                          order_send=lambda req:sent.append(req))
    with pytest.raises(BrokerError):
        broker_with_api(api)._send({})
    assert sent == []


@pytest.mark.parametrize("response",[None,SimpleNamespace(retcode=10012),SimpleNamespace(retcode=10031)])
def test_uncertain_broker_reply_is_distinct_from_rejection(response):
    api = SimpleNamespace(**api_constants(), order_check=lambda req:SimpleNamespace(retcode=0),order_send=lambda req:response)
    with pytest.raises(UncertainExecution):
        broker_with_api(api)._send({})


def test_filling_policy_is_derived_from_symbol_flags():
    sent = []
    api = SimpleNamespace(**api_constants(),order_check=lambda req:SimpleNamespace(retcode=0),
                          order_send=lambda req:sent.append(dict(req)) or SimpleNamespace(retcode=10009))
    broker = broker_with_api(api)
    broker.info["filling_mode"] = 2
    broker._send({})
    assert sent[0]["type_filling"] == api.ORDER_FILLING_IOC


def test_disabled_python_trading_never_sends():
    broker = broker_with_api(SimpleNamespace())
    broker.account = lambda:{"trade_allowed":False,"hedging":True}
    with pytest.raises(BrokerError):
        broker._send({})


def test_netting_account_rejected_before_send():
    broker = broker_with_api(SimpleNamespace())
    broker.account = lambda:{"trade_allowed":True,"hedging":False}
    with pytest.raises(BrokerError):
        broker._send({})


def test_exact_minimum_stop_distance_is_allowed():
    assert valid_stop("buy", 4153.5, 4154, 4154.2, .5)
    assert valid_stop("sell", 4154.7, 4154, 4154.2, .5)
    assert not valid_stop("buy", 4154, 4154, 4154.2, 0)


def test_tp_update_preserves_stop_and_breakeven_preserves_tp():
    sent = []
    position = {"ticket": 123, "sl": 84842.5, "tp": 0.0}
    broker = broker_with_api(SimpleNamespace(TRADE_ACTION_SLTP=6))
    broker.info.update(digits=2)
    broker.symbol = "BTCUSD"
    broker.positions = lambda: [dict(position)]
    broker._send = lambda request, trade: sent.append(request) or position.update(sl=request["sl"], tp=request["tp"])
    broker.modify(123, target=84857.506)
    assert sent[0]["sl"] == 84842.5 and sent[0]["tp"] == 84857.51
    broker.modify(123, 84843)
    assert sent[1]["sl"] == 84843 and sent[1]["tp"] == 84857.51


def test_broker_deal_reports_native_tp_reason():
    deal = SimpleNamespace(ticket=1, position_id=2, time=3, profit=1, commission=0, swap=0, fee=0,
                           entry=1, volume=.01, price=84857.5, symbol="BTCUSD", comment="[tp]", type=1, reason=5)
    broker = broker_with_api(SimpleNamespace(DEAL_REASON_TP=5, DEAL_REASON_SL=4,
                                             history_deals_get=lambda **kwargs: [deal]))
    broker.identity = "test"
    assert broker.deals(2)[0]["reason"] == "take_profit"
    deal.reason = 4
    assert broker.deals(2)[0]["reason"] == "stop_loss"


def test_catalog_contains_all_pairs_and_puts_market_watch_first():
    symbols = [SimpleNamespace(name=name, description=name, visible=visible)
               for name, visible in [("XAUUSD", True), ("GBPUSD", False), ("EURUSD", True), ("USDJPY", True)]]
    catalog = symbol_catalog(symbols)
    assert [s["name"] for s in catalog] == ["EURUSD", "USDJPY", "XAUUSD", "GBPUSD"]
    assert symbol_catalog([]) == []
    with pytest.raises(BrokerError):
        symbol_catalog(None)


def test_mt5_connect_accepts_non_gold_symbol(monkeypatch):
    import sys
    from collections import namedtuple
    Info = namedtuple("Info", "description digits point volume_min volume_max volume_step trade_stops_level filling_mode")
    api = SimpleNamespace(initialize=lambda *args, **kw: True,
                          account_info=lambda: SimpleNamespace(server="test", login=123),
                          symbol_select=lambda symbol, visible: symbol == "EURUSD",
                          symbol_info=lambda symbol: Info("Euro / US Dollar", 5, .00001, .01, 100, .01, 0, 1),
                          shutdown=lambda: None)
    monkeypatch.setitem(sys.modules, "MetaTrader5", api)
    monkeypatch.setattr("signaldesk.broker.discover_terminals", lambda: [])
    broker = MT5Broker("test-terminal.exe", "EURUSD")
    assert broker.symbol == "EURUSD"


def test_account_profile_selects_symbol_before_old_symbol_is_validated(monkeypatch):
    import sys
    from collections import namedtuple
    Info = namedtuple("Info", "description digits point volume_min volume_max volume_step trade_stops_level filling_mode")
    selected = []
    api = SimpleNamespace(initialize=lambda *args, **kwargs: True,
                          account_info=lambda: SimpleNamespace(server="new-server", login=222),
                          symbol_select=lambda symbol, visible: selected.append(symbol) or symbol == "XAUUSD",
                          symbol_info=lambda symbol: Info("Gold", 2, .01, .01, 100, .01, 0, 1),
                          shutdown=lambda: None)
    monkeypatch.setitem(sys.modules, "MetaTrader5", api)
    monkeypatch.setattr("signaldesk.broker.discover_terminals", lambda: [])
    def profile(identity):
        assert identity == "new-server:222"
        return "XAUUSD"
    broker = MT5Broker("terminal.exe", "BTCUSD", account_settings=profile)
    assert selected == ["XAUUSD"] and broker.symbol == "XAUUSD"


def test_changed_account_rejected_before_requesting_price():
    queried = []
    broker = MT5Broker.__new__(MT5Broker)
    broker.identity, broker.symbol = "old-server:111", "BTCUSD"
    broker.mt5 = SimpleNamespace(account_info=lambda: SimpleNamespace(server="new-server", login=222),
                                 terminal_info=lambda: SimpleNamespace(connected=True),
                                 symbol_info_tick=lambda symbol: queried.append(symbol))
    with pytest.raises(AccountChanged):
        broker.tick()
    assert queried == []


@pytest.mark.parametrize("offset", [-25200, 25200])
def test_clock_offset_requires_new_tick_and_still_rejects_frozen_feed(monkeypatch, offset):
    wall = [1_800_000_000.0]
    mono = [100.0]
    tick = SimpleNamespace(bid=84905, ask=84915, time=wall[0] + offset,
                           time_msc=int((wall[0] + offset) * 1000))
    broker = broker_with_api(SimpleNamespace(symbol_info_tick=lambda symbol: tick))
    broker.symbol = "BTCUSD"
    monkeypatch.setattr("signaldesk.broker.time.time", lambda: wall[0])
    monkeypatch.setattr("signaldesk.broker.time.monotonic", lambda: mono[0])
    with pytest.raises(BrokerError, match="قديم"):
        broker.tick()
    wall[0] += 2
    mono[0] += 2
    tick.time += 2
    tick.time_msc += 2000
    assert broker.tick()["ask"] == 84915
    wall[0] += 31
    mono[0] += 31
    with pytest.raises(BrokerError, match="قديم"):
        broker.tick()
