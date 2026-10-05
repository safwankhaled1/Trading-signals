from __future__ import annotations

import math
import time
from decimal import Decimal, ROUND_DOWN

MAGIC = 26010394


class BrokerError(RuntimeError):
    pass


class AccountChanged(BrokerError):
    pass


class UncertainExecution(BrokerError):
    """A request may have executed; never retry it blindly."""


def quantize_volume(volume, minimum, maximum, step):
    dstep = Decimal(str(step))
    value = (Decimal(str(volume)) / dstep).to_integral_value(rounding=ROUND_DOWN) * dstep
    if value < Decimal(str(minimum)) or value > Decimal(str(maximum)):
        raise BrokerError("حجم الصفقة خارج حدود الوسيط")
    return float(value)


def valid_stop(side, stop, bid, ask, minimum=0):
    if side == "buy":
        return stop < bid and bid - stop + 1e-8 >= minimum
    return stop > ask and stop - ask + 1e-8 >= minimum


def is_gold_symbol(name, description=""):
    return "XAU" in name.upper() or "GOLD" in name.upper() or "gold" in description.lower()


def symbol_instrument(name, description=""):
    if is_gold_symbol(name, description):
        return "XAU"
    if "BTC" in name.upper() or "BITCOIN" in name.upper() or "bitcoin" in description.lower():
        return "BTC"
    return name.upper()


class DemoBroker:
    mode = "demo"

    def __init__(self, store):
        self.store = store
        saved = store.get("demo_broker", {})
        self.bid = saved.get("bid", 4154.0)
        self.spread = 0.20
        self.open_positions = saved.get("positions", [])
        self.history = saved.get("deals", [])
        self.counter = saved.get("counter", 10000)
        self.connected = True
        self.identity = "simulation"
        self.symbol = "XAUUSD"
        self.info = {"volume_min": .01, "volume_step": .01, "volume_max": 100., "digits": 2, "point": .01, "trade_stops_level": 0}

    def persist(self):
        self.store.set("demo_broker", {"bid": self.bid, "positions": self.open_positions, "deals": self.history, "counter": self.counter})

    def account(self):
        balance = 10000 + sum(d["net"] for d in self.history)
        return {"login": "محاكاة", "server": "محاكي محلي", "currency": "USD", "balance": round(balance, 2),
                "equity": round(balance + sum(p["profit"] for p in self.positions()), 2), "hedging": True, "trade_allowed": True}

    def tick(self):
        return {"bid": self.bid, "ask": round(self.bid + self.spread, 5), "time": time.time()}

    def set_price(self, value):
        if not math.isfinite(value) or value <= 0:
            raise BrokerError("سعر المحاكاة غير صالح")
        self.bid = value
        for p in list(self.open_positions):
            price = self.bid if p["side"] == "buy" else self.tick()["ask"]
            stopped = p["sl"] and (price <= p["sl"] if p["side"] == "buy" else price >= p["sl"])
            targeted = p.get("tp", 0) and (price >= p["tp"] if p["side"] == "buy" else price <= p["tp"])
            if stopped or targeted:
                self.close(p["ticket"], p["volume"], "broker-stop" if stopped else "broker-target")
        self.persist()

    def positions(self):
        output = []
        for p in self.open_positions:
            item = dict(p)
            quote = self.bid if p["side"] == "buy" else self.tick()["ask"]
            item["profit"] = round((quote - p["entry"]) * (1 if p["side"] == "buy" else -1) * 100 * p["volume"], 2)
            output.append(item)
        return output

    def loss_per_lot(self, side, entry, stop):
        return abs(entry - stop) * 100

    def open(self, side, volume, stop, comment):
        self.counter += 1
        price = self.tick()["ask"] if side == "buy" else self.bid
        position = {"ticket": self.counter, "position_id": self.counter, "side": side, "volume": volume, "entry": price,
                    "sl": stop, "tp": 0., "symbol": self.symbol, "magic": MAGIC, "comment": comment, "time": time.time()}
        self.open_positions.append(position)
        self.persist()
        return dict(position)

    def close(self, ticket, volume, comment):
        p = next((x for x in self.open_positions if x["ticket"] == ticket), None)
        if p is None:
            raise BrokerError("الصفقة مغلقة بالفعل")
        volume = min(volume, p["volume"])
        price = self.bid if p["side"] == "buy" else self.tick()["ask"]
        self.counter += 1
        net = (price - p["entry"]) * (1 if p["side"] == "buy" else -1) * 100 * volume
        self.history.append({"id": f"demo:{self.counter}", "position_id": p["position_id"], "ticket": ticket,
                             "time": time.time(), "net": round(net, 2), "profit": round(net, 2), "commission": 0., "swap": 0.,
                             "fee": 0., "entry_type": 1, "volume": volume, "price": price, "symbol": self.symbol, "comment": comment,
                             "reason": "take_profit" if comment == "broker-target" else "stop_loss" if comment == "broker-stop" else ""})
        p["volume"] = round(p["volume"] - volume, 8)
        if p["volume"] < .001:
            self.open_positions.remove(p)
        self.persist()
        return {"volume": volume, "price": price}

    def modify(self, ticket, stop=None, target=None):
        p = next((x for x in self.open_positions if x["ticket"] == ticket), None)
        if p is None:
            raise BrokerError("الصفقة غير موجودة")
        if stop is not None and not valid_stop(p["side"], stop, self.bid, self.tick()["ask"]):
            raise BrokerError("سعر التأمين غير صالح حاليًا؛ ستتم المحاولة عند سماح السعر")
        if stop is not None:
            p["sl"] = stop
        if target is not None:
            p["tp"] = target
        self.persist()

    def deals(self, position_id=None):
        return [d for d in self.history if position_id is None or d["position_id"] == position_id]

    def shutdown(self):
        self.persist()


def discover_terminals():
    import psutil
    from pathlib import Path
    import os
    paths = set()
    for process in psutil.process_iter(["name", "exe"]):
        try:
            if (process.info["name"] or "").lower() in {"terminal64.exe", "terminal.exe"} and process.info["exe"]:
                paths.add(process.info["exe"])
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            pass
    for key in ("ProgramFiles", "ProgramFiles(x86)"):
        root = Path(os.environ.get(key, "C:/Program Files"))
        if root.exists():
            for path in root.glob("*/terminal64.exe"):
                paths.add(str(path))
    return sorted(paths)


def symbol_catalog(symbols):
    """Keep Market Watch symbols first while exposing the broker's full catalog."""
    if symbols is None:
        raise BrokerError("تعذر قراءة رموز الوسيط")
    return [{"name": s.name, "description": s.description, "visible": bool(s.visible)}
            for s in sorted(symbols, key=lambda s: (not s.visible, s.name.casefold()))]


class MT5Broker:
    mode = "live"

    def __init__(self, terminal_path="", symbol="", account_settings=None):
        import MetaTrader5 as mt5
        self.mt5 = mt5
        terminals = discover_terminals()
        if not terminal_path and len(terminals) > 1:
            raise BrokerError("اكتُشفت عدة نسخ MT5؛ اختر النسخة من صفحة الاتصالات")
        path = terminal_path or (terminals[0] if terminals else "")
        ok = mt5.initialize(path, timeout=10000) if path else mt5.initialize(timeout=10000)
        if not ok:
            raise BrokerError("تعذر الاتصال بـMT5: " + str(mt5.last_error()))
        try:
            self.connected = True
            account = mt5.account_info()
            if account is None:
                raise BrokerError("افتح MT5 وسجل الدخول إلى الحساب أولًا")
            self.identity = f"{account.server}:{account.login}"
            if account_settings:
                symbol = account_settings(self.identity)
            if not symbol:
                candidates = self.gold_symbols()
                visible = [s.name for s in candidates if s.visible]
                choices = visible or [s.name for s in candidates]
                if not choices:
                    choices = [s["name"] for s in self.symbols() if s["visible"]]
                if len(choices) != 1:
                    raise BrokerError("اختر رمز التداول من صفحة الاتصالات")
                symbol = choices[0]
            if not mt5.symbol_select(symbol, True):
                raise BrokerError("تعذر تفعيل رمز التداول: " + symbol)
            info = mt5.symbol_info(symbol)
            if info is None:
                raise BrokerError("رمز التداول غير موجود لدى الوسيط: " + symbol)
            self.symbol, self.info = symbol, info._asdict()
            self.path = path
        except Exception:
            mt5.shutdown()
            raise

    def gold_symbols(self):
        symbols = self.mt5.symbols_get()
        if symbols is None:
            raise BrokerError("تعذر قراءة رموز الوسيط")
        return [s for s in symbols if is_gold_symbol(s.name, s.description)]

    def symbols(self):
        return symbol_catalog(self.mt5.symbols_get())

    def account(self):
        a = self.mt5.account_info()
        t = self.mt5.terminal_info()
        if not a or not t or not t.connected:
            raise BrokerError("اتصال MT5 غير متاح")
        if f"{a.server}:{a.login}" != self.identity:
            raise AccountChanged("تغيّر حساب MT5؛ جارٍ تحميل بيانات الحساب الجديد")
        return {"login": a.login, "server": a.server, "currency": a.currency, "balance": a.balance, "equity": a.equity,
                "hedging": a.margin_mode == self.mt5.ACCOUNT_MARGIN_MODE_RETAIL_HEDGING,
                "trade_allowed": bool(a.trade_allowed and a.trade_expert and t.trade_allowed and not t.tradeapi_disabled)}

    def tick(self):
        self.account()
        t = self.mt5.symbol_info_tick(self.symbol)
        if t is None or not t.bid or not t.ask:
            raise BrokerError("لا يوجد سعر صالح للرمز: " + self.symbol)
        stamp = getattr(t, "time_msc", 0) or t.time * 1000
        observed = time.monotonic()
        previous = getattr(self, "_last_tick_stamp", None)
        if previous is not None and stamp > previous:
            self._last_tick_observed = observed
        elif previous is None or stamp < previous:
            self._last_tick_observed = None
        self._last_tick_stamp = stamp
        last_update = getattr(self, "_last_tick_observed", None)
        recent_update = last_update is not None and observed - last_update <= 30
        # A broker/device clock offset must not classify a live stream as frozen.
        # Never trust a mismatched timestamp until a new tick has been observed.
        timestamp_age = time.time() - t.time
        if not (-5 <= timestamp_age <= 30 or recent_update):
            raise BrokerError("سعر الرمز قديم؛ بانتظار تحديث من الوسيط")
        return {"bid": t.bid, "ask": t.ask, "time": t.time}

    def positions(self):
        self.account()
        positions = self.mt5.positions_get(symbol=self.symbol)
        if positions is None:
            raise BrokerError("تعذر قراءة الصفقات؛ لا تعتبر مغلقة")
        return [{"ticket": p.ticket, "position_id": p.identifier, "side": "buy" if p.type == 0 else "sell",
                 "volume": p.volume, "entry": p.price_open, "sl": p.sl, "tp": p.tp, "profit": p.profit,
                 "symbol": p.symbol, "magic": p.magic, "comment": p.comment, "time": p.time} for p in positions]

    def loss_per_lot(self, side, entry, stop):
        value = self.mt5.order_calc_profit(self.mt5.ORDER_TYPE_BUY if side == "buy" else self.mt5.ORDER_TYPE_SELL,
                                           self.symbol, 1.0, entry, stop)
        if value is None or abs(value) < 1e-10:
            raise BrokerError("تعذر حساب المخاطرة بعملة الحساب")
        return abs(value)

    def _send(self, request, trade=True):
        a = self.account()
        if not a["trade_allowed"]:
            raise BrokerError("فعّل التداول الآلي وواجهة Python في MT5")
        if not a["hedging"]:
            raise BrokerError("إدارة الإشارات المستقلة تحتاج حساب Hedging")
        m = self.mt5
        if trade:
            flags = self.info["filling_mode"]
            request["type_filling"] = m.ORDER_FILLING_FOK if flags & 1 else m.ORDER_FILLING_IOC if flags & 2 else m.ORDER_FILLING_RETURN
        check = m.order_check(request)
        if check is None or check.retcode != 0:
            raise BrokerError("رفض فحص الأمر: " + (check.comment if check else str(m.last_error())))
        result = m.order_send(request)
        if result is None or result.retcode in {m.TRADE_RETCODE_TIMEOUT, m.TRADE_RETCODE_CONNECTION}:
            raise UncertainExecution("نتيجة التنفيذ غير مؤكدة؛ يجب مطابقتها مع سجل الوسيط")
        if result.retcode not in {m.TRADE_RETCODE_DONE, m.TRADE_RETCODE_DONE_PARTIAL, m.TRADE_RETCODE_NO_CHANGES}:
            raise BrokerError(f"رفض الوسيط ({result.retcode}): {result.comment}")
        return result

    def open(self, side, volume, stop, comment):
        m, tick = self.mt5, self.tick()
        result = self._send({"action": m.TRADE_ACTION_DEAL, "symbol": self.symbol, "volume": volume,
                             "type": m.ORDER_TYPE_BUY if side == "buy" else m.ORDER_TYPE_SELL,
                             "price": tick["ask"] if side == "buy" else tick["bid"], "sl": round(stop, self.info["digits"]),
                             "deviation": 20, "magic": MAGIC, "comment": comment})
        positions = self.positions()
        p = next((p for p in positions if p["ticket"] == result.order or p["comment"] == comment), None)
        if p is None:
            deals = m.history_deals_get(ticket=result.deal)
            if deals:
                pid = deals[0].position_id
                p = next((p for p in positions if p["position_id"] == pid), None)
                if not p:
                    # The broker may already have closed a freshly opened position.
                    p = {"ticket": pid, "position_id": pid, "entry": result.price, "volume": result.volume,
                         "sl": stop, "tp": 0, "side": side, "symbol": self.symbol, "time": time.time(), "magic": MAGIC, "comment": comment}
        if p is None:
            raise UncertainExecution("تم قبول الدخول لكن تعذر تحديد الصفقة؛ لن يتم تكرار الطلب")
        return p

    def close(self, ticket, volume, comment):
        m, tick = self.mt5, self.tick()
        p = next((p for p in self.positions() if p["ticket"] == ticket), None)
        if not p:
            raise BrokerError("الصفقة لم تعد مفتوحة")
        result = self._send({"action": m.TRADE_ACTION_DEAL, "symbol": self.symbol, "position": ticket, "volume": volume,
                             "type": m.ORDER_TYPE_SELL if p["side"] == "buy" else m.ORDER_TYPE_BUY,
                             "price": tick["bid"] if p["side"] == "buy" else tick["ask"],
                             "deviation": 20, "magic": MAGIC, "comment": comment})
        return {"volume": result.volume, "price": result.price}

    def modify(self, ticket, stop=None, target=None):
        p = next((p for p in self.positions() if p["ticket"] == ticket), None)
        if not p:
            raise BrokerError("الصفقة لم تعد مفتوحة")
        self._send({"action": self.mt5.TRADE_ACTION_SLTP, "symbol": self.symbol, "position": ticket,
                    "sl": p["sl"] if stop is None else round(stop, self.info["digits"]),
                    "tp": p["tp"] if target is None else round(target, self.info["digits"])}, trade=False)

    def deals(self, position_id=None):
        from datetime import datetime, timezone
        self.account()
        m = self.mt5
        ds = m.history_deals_get(position=position_id) if position_id else m.history_deals_get(datetime(2020, 1, 1, tzinfo=timezone.utc), datetime.now(timezone.utc))
        if ds is None:
            raise BrokerError("تعذر قراءة سجل الصفقات")
        return [{"id": f"{self.identity}:{d.ticket}", "position_id": d.position_id, "time": d.time, "net": d.profit + d.commission + d.swap + d.fee,
                 "profit": d.profit, "commission": d.commission, "swap": d.swap, "fee": d.fee, "entry_type": d.entry,
                 "volume": d.volume, "price": d.price, "symbol": d.symbol, "comment": d.comment,
                 "reason": "take_profit" if getattr(d, "reason", -2) == getattr(m, "DEAL_REASON_TP", -1)
                           else "stop_loss" if getattr(d, "reason", -2) == getattr(m, "DEAL_REASON_SL", -1) else ""}
                for d in ds if d.type in (0, 1)]

    def shutdown(self):
        self.mt5.shutdown()
