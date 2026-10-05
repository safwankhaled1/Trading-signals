from __future__ import annotations

import hashlib
import time
from dataclasses import replace
from copy import deepcopy

from .broker import AccountChanged, BrokerError, UncertainExecution, quantize_volume, valid_stop, symbol_instrument
from .config import Settings
from .parser import expand_price, parse_message


class TradingEngine:
    """Single writer. The GUI never performs broker calls or modifies trade state."""

    def __init__(self, store, broker, settings=None, clock=time.time):
        self.store, self.broker = store, broker
        self.store.activate_account(broker.identity)
        self.settings = settings or Settings()
        self.clock = clock
        self.signals = {s["id"]: s for s in store.signals()}
        for signal in self.signals.values():
            if not signal.get("symbol") and not signal.get("config", {}).get("symbol"):
                instrument = parse_message(signal.get("raw", "")).instrument
                if signal.get("account") == broker.identity and instrument == symbol_instrument(broker.symbol, broker.info.get("description", "")):
                    signal["symbol"] = broker.symbol
                    store.save_signal(signal)
        self.paused = store.get("paused", False)
        self.last_history = 0
        self.last_error = ""
        self.needs_reconcile = False
        self.history_cursor = 0

    def settings_for(self, channel):
        profile = self.store.get(f"profile:{channel}")
        return Settings.from_dict(profile) if profile else deepcopy(self.settings)

    def save(self, signal):
        self.signals[signal["id"]] = signal
        self.store.save_signal(signal)

    def current_signals(self):
        return [s for s in self.signals.values() if s.get("account") == self.broker.identity
                and s.get("symbol", s.get("config", {}).get("symbol") or "XAUUSD") == self.broker.symbol]

    def linked(self, channel, reply):
        if reply:
            row = self.store.message(channel, reply)
            return self.signals.get(row["signal"]) if row and row["signal"] else None
        candidates = [s for s in self.current_signals() if s["channel"] == channel and not s.get("manual")]
        return max(candidates, key=lambda s: (s["created"], s.get("sequence", 0)), default=None)

    def receive(self, channel, message_id, raw, reply=None, edited=False, received=None, channel_name="", tick=None):
        now = self.clock() if received is None else received
        old = self.store.message(channel, message_id)
        if old and not edited:
            return old["signal"]
        parsed = parse_message(raw)
        reference = self.signals.get(old["signal"]) if edited and old else self.linked(channel, reply)
        if reference and reference not in self.current_signals():
            reference = None
        signal_id = reference["id"] if reference else ""
        if parsed.kind == "repeat" and not parsed.entry and not edited:
            # A fresh availability message refers to its original entry text, even if an
            # older parser failed to understand that entry. Never replay the original post.
            rows = [self.store.message(channel, reply)] if reply else self.store.latest_messages(channel)
            for row in rows:
                if not row:
                    continue
                source = parse_message(row["text"])
                if source.kind in {"entry", "repeat"} and source.entry:
                    parsed = replace(parsed, side=parsed.side or source.side, entry=source.entry,
                                     stop=parsed.stop or source.stop, instrument=parsed.instrument or source.instrument)
                    break
        if parsed.kind == "ambiguous":
            signal_id = ""
        self.store.save_message(channel, message_id, raw, reply, signal_id, now, edited)
        cfg = self.settings_for(channel)
        try:
            if parsed.kind == "ambiguous":
                raise BrokerError(parsed.reason)
            if parsed.instrument and parsed.instrument != symbol_instrument(self.broker.symbol, self.broker.info.get("description", "")):
                raise BrokerError("الإشارة لرمز مختلف؛ لن تُنفّذ على الرمز المختار " + self.broker.symbol)
            if edited:
                if cfg.follow_edits and reference:
                    self._edit(reference, parsed, cfg)
                else:
                    self.store.event("تعديل الرسالة محفوظ؛ متابعة التعديلات معطلة", signal_id)
                return signal_id
            if parsed.kind == "ignored":
                self.store.event("رسالة غير تداولية؛ تم تجاهلها", signal_id, message_id=message_id)
                return ""
            if parsed.kind in {"targets", "manage"}:
                managed_manuals = [s for s in self.current_signals() if s.get("manual") and s["state"] == "open" and self._manual_enabled(s)]
                if parsed.kind == "targets" and cfg.targets_mode == "channel":
                    for manual in managed_manuals:
                        manual["channel_targets"] = parsed.targets
                        self._set_targets(manual, self.settings)
                        self.save(manual)
                if not reference:
                    if managed_manuals and parsed.kind == "targets" and cfg.targets_mode == "channel":
                        return ""
                    self.store.event("لم توجد إشارة مرتبطة بالتحديث", level="warning")
                    return ""
                if parsed.kind == "targets" and cfg.targets_mode == "channel":
                    reference["channel_targets"] = parsed.targets
                    reference["config"]["targets_mode"] = "channel"
                    reference["config"]["channel_target_lots"] = cfg.channel_target_lots
                    self._set_targets(reference, cfg)
                    self.save(reference)
                    self.store.event("تم ربط أهداف القناة بالإشارة", signal_id, targets=parsed.targets)
                elif parsed.kind == "manage" and cfg.channel_management and reference["state"] == "open":
                    reference["be_requested"] = True
                    reference["config"]["breakeven_pips"] = cfg.breakeven_pips
                    self.save(reference)
                    self._secure(reference)
                else:
                    self.store.event("التحديث محفوظ؛ الميزة معطلة أو لا توجد صفقة مفتوحة", signal_id)
                return signal_id
            tick = tick or self.broker.tick()
            side = parsed.side or (reference["side"] if reference else "")
            if not side:
                raise BrokerError("لا توجد إشارة سابقة لتحديد اتجاه إعادة الدخول")
            quote = tick["ask"] if side == "buy" else tick["bid"]
            entry = expand_price(parsed.entry, quote, cfg.max_price_inference_distance) if parsed.entry else reference["entry"] if reference else None
            if entry is None:
                raise BrokerError("لا يوجد سعر دخول للإشارة")
            same = reference and side == reference["side"] and abs(entry - reference["entry"]) < 1e-6
            root = reference["root"] if parsed.kind == "repeat" and same else ""
            if parsed.kind == "repeat" and same:
                active = [s for s in self.current_signals() if s["root"] == root and s["state"] in {"pending", "sending", "uncertain", "open"}]
                # Synchronize closures before deciding whether a new message permits re-entry.
                self.reconcile()
                active = [s for s in active if s["state"] in {"pending", "sending", "uncertain", "open"}]
                if active:
                    self.store.event("لم يتم التكرار: الصفقة السابقة مفتوحة أو الإشارة منتظرة", signal_id)
                    return signal_id
            if parsed.kind == "repeat" and not parsed.entry and not reference:
                raise BrokerError("إعادة دخول بدون إشارة مرتبطة")
            identifier = hashlib.sha256(f"{self.broker.identity}:{channel}:{message_id}".encode()).hexdigest()[:16]
            stop = expand_price(parsed.stop, entry, cfg.max_price_inference_distance) if parsed.stop else reference["signal_stop"] if same else None
            signal = {"id": identifier, "root": root or identifier, "channel": channel, "channel_name": channel_name or cfg.channel_name or str(channel),
                      "message": message_id, "raw": raw, "reply": reply, "account": self.broker.identity, "symbol": self.broker.symbol,
                      "side": side, "entry": entry, "signal_stop": stop, "created": now,
                      "expires": now + cfg.wait_minutes * 60 if cfg.expiry_enabled else None,
                      "sequence": max((s.get("sequence", 0) for s in self.signals.values()), default=0) + 1,
                      "state": "pending", "config": cfg.to_dict(), "channel_targets": parsed.targets or (deepcopy(reference.get("channel_targets", [])) if parsed.kind == "repeat" and same else []),
                      "targets": [], "completed": [], "realized": 0., "be_requested": False, "secured": False}
            self.save(signal)
            self.store.save_message(channel, message_id, raw, reply, identifier, now)
            self.store.event("استُقبلت إشارة دخول", identifier, entry=entry, side=side, quote=quote)
            self._try_entry(signal, tick)
            return identifier
        except (ValueError, BrokerError) as exc:
            self.store.event(str(exc), signal_id, "warning", message_id=message_id)
            return signal_id

    def _edit(self, s, parsed, cfg):
        if s["state"] not in {"pending", "open"}:
            return
        if parsed.kind == "targets" and cfg.targets_mode == "channel":
            s["channel_targets"] = parsed.targets
            self._set_targets(s, Settings.from_dict(s["config"]))
        if parsed.kind in {"entry", "repeat"}:
            if s["state"] == "pending":
                tick = self.broker.tick()
                side = parsed.side or s["side"]
                s["side"] = side
                if parsed.entry:
                    s["entry"] = expand_price(parsed.entry, tick["ask"] if side == "buy" else tick["bid"], cfg.max_price_inference_distance)
            if parsed.stop:
                value = expand_price(parsed.stop, s["entry"], cfg.max_price_inference_distance)
                if s["state"] == "open":
                    tick = self.broker.tick()
                    if not valid_stop(s["side"], value, tick["bid"], tick["ask"], self.stop_minimum()):
                        raise BrokerError("الستوب المعدّل غير صالح عند الوسيط")
                    # Keep a secured stop; an edit must not silently undo completed breakeven.
                    if s["secured"]:
                        value = max(value, s["sl"]) if s["side"] == "buy" else min(value, s["sl"])
                    self.broker.modify(s["ticket"], value)
                    s["sl"] = value
                s["signal_stop"] = value
        self.save(s)
        self.store.event("طُبّق تعديل الرسالة", s["id"])

    def stop_minimum(self):
        return self.broker.info["trade_stops_level"] * self.broker.info["point"]

    def _try_entry(self, s, tick):
        cfg = Settings.from_dict(s["config"])
        if s["expires"] is not None and self.clock() >= s["expires"]:
            s["state"] = "expired"
            self.save(s)
            self.store.event("انتهت مهلة انتظار الإشارة", s["id"])
            return
        if self.paused:
            return
        price = tick["ask"] if s["side"] == "buy" else tick["bid"]
        if abs(price - s["entry"]) > cfg.entry_margin + 1e-8:
            self._entry_error(s, f"بانتظار السعر: {'ASK' if s['side'] == 'buy' else 'BID'} {price:g} خارج نطاق {s['entry'] - cfg.entry_margin:g} — {s['entry'] + cfg.entry_margin:g}")
            return
        stop = s["signal_stop"]
        if cfg.stop_mode == "fixed" or not stop or not valid_stop(s["side"], stop, tick["bid"], tick["ask"], self.stop_minimum()):
            stop = price - cfg.stop_distance if s["side"] == "buy" else price + cfg.stop_distance
        stop = round(stop, self.broker.info["digits"])
        if not valid_stop(s["side"], stop, tick["bid"], tick["ask"], self.stop_minimum()):
            spread = tick["ask"] - tick["bid"]
            self._entry_error(s, f"مسافة الستوب أصغر من متطلبات الوسيط أو السبريد. السبريد {spread:g} وأقل مسافة للوسيط {self.stop_minimum():g}؛ عدّل مسافة الستوب في الإعدادات")
            return
        try:
            requested = cfg.fixed_lot if cfg.size_mode == "fixed" else self.broker.account()["balance"] * cfg.risk_percent / 100 / self.broker.loss_per_lot(s["side"], price, stop)
            info = self.broker.info
            volume = quantize_volume(requested, info["volume_min"], info["volume_max"], info["volume_step"])
            if cfg.size_mode == "fixed" and abs(volume - requested) > 1e-8:
                raise BrokerError("اللوت الثابت لا يتوافق مع خطوة اللوت عند الوسيط")
            action = f"entry:{s['id']}"
            comment = f"GSD:{s['id']}"
            if not self.store.claim(action, s["id"], "entry", {"comment": comment, "volume": volume, "stop": stop}):
                return
            s["state"] = "sending"
            self.save(s)
            try:
                started = time.monotonic()
                s["entry_request_delay_ms"] = round(max(0, self.clock() - s["created"]) * 1000, 1)
                p = self.broker.open(s["side"], volume, stop, comment)
                s["broker_roundtrip_ms"] = round((time.monotonic() - started) * 1000, 1)
            except UncertainExecution as exc:
                self.store.finish_action(action, "uncertain", {"error": str(exc), "comment": comment})
                s["state"] = "uncertain"
                self.save(s)
                self.store.event(str(exc), s["id"], "error")
                return
            except BrokerError as exc:
                self.store.finish_action(action, "rejected", {"error": str(exc)})
                s["state"] = "rejected"
                self.save(s)
                self.store.event(str(exc), s["id"], "warning")
                return
            self.store.finish_action(action, "done", p)
            self._attach(s, p)
            self.store.event("أكد MT5 فتح الصفقة" if self.broker.mode == "live" else "تم فتح صفقة محاكاة", s["id"], ticket=p["ticket"], price=p["entry"], volume=p["volume"], request_delay_ms=s["entry_request_delay_ms"], broker_roundtrip_ms=s["broker_roundtrip_ms"])
        except BrokerError as exc:
            self._entry_error(s, str(exc))

    def _entry_error(self, s, message):
        if message.startswith("بانتظار السعر:"):
            if s.get("last_error", "").startswith("بانتظار السعر:") and self.clock() - s.get("price_notice_time", 0) < 10:
                return
            s["price_notice_time"] = self.clock()
        if s.get("last_error") != message:
            s["last_error"] = message
            self.save(s)
            self.store.event(message, s["id"], "warning")

    def _attach(self, s, p, sync_target=True):
        s.update(state="open", ticket=p["ticket"], position_id=p["position_id"], fill=p["entry"], volume=p["volume"], initial_volume=p["volume"],
                 sl=p["sl"], tp=p.get("tp", 0), opened=p["time"], symbol=p["symbol"], profit=p.get("profit", 0))
        self._set_targets(s, Settings.from_dict(s["config"]))
        self.save(s)
        if sync_target:
            self._sync_target(s)

    def _sync_target(self, s):
        cfg = Settings.from_dict(s["config"])
        if s["state"] != "open" or not s["targets"] or not cfg.native_tp_enabled:
            return
        if s.get("manual") and (not cfg.manage_manual_stops or not self._manual_enabled(s)):
            return
        target = round(s["targets"][-1]["price"], self.broker.info["digits"])
        if abs(s.get("tp", 0) - target) < self.broker.info["point"] / 2:
            return
        if s.get("tp_retry_at", 0) > self.clock():
            return
        try:
            # Changing TP must preserve the latest broker SL, including manual edits.
            self.broker.modify(s["ticket"], target=target)
            s.update(tp=target, broker_target=target, tp_error="")
            s.pop("tp_retry_at", None)
            self.save(s)
            self.store.event("تم تثبيت آخر هدف TP في MT5" if self.broker.mode == "live" else "تم تثبيت آخر هدف لدى المحاكي",
                             s["id"], target=target)
        except BrokerError as exc:
            s.update(tp_error=str(exc), tp_retry_at=self.clock() + 5)
            self.save(s)
            self.store.event("تعذر تثبيت TP؛ إدارة الأهداف داخل التطبيق مستمرة: " + str(exc), s["id"], "warning")

    def _set_targets(self, s, cfg):
        if cfg.targets_mode == "none":
            s["targets"] = []
            return
        if cfg.targets_mode == "channel":
            levels = sorted(set(s.get("channel_targets", [])), reverse=s["side"] == "sell")
            anchor = s.get("fill", s["entry"])
            levels = [v for v in levels if v > anchor] if s["side"] == "buy" else [v for v in levels if v < anchor]
            s["targets"] = [{"price": p, "lot": cfg.channel_target_lots[i] if i < len(cfg.channel_target_lots) else 0., "key": f"stage:{i}"} for i, p in enumerate(levels)]
        else:
            base = s.get("fill", s["entry"])
            direction = 1 if s["side"] == "buy" else -1
            s["targets"] = [{"price": round(base + direction * st["pips"] * cfg.pip_size, 5), "lot": st["lot"], "key": f"stage:{i}"} for i, st in enumerate(cfg.stages)]

    def _secure(self, s):
        if s.get("secured") or s["state"] != "open":
            return
        cfg = Settings.from_dict(s["config"])
        stop = s["fill"] + (1 if s["side"] == "buy" else -1) * cfg.breakeven_pips * cfg.pip_size
        if (s["side"] == "buy" and s["sl"] >= stop) or (s["side"] == "sell" and s["sl"] and s["sl"] <= stop):
            s["secured"] = True
            self.save(s)
            return
        try:
            self.broker.modify(s["ticket"], stop)
            s.update(sl=stop, secured=True)
            self.save(s)
            self.store.event("تم تأمين المتبقي", s["id"], stop=stop)
        except BrokerError as exc:
            self._entry_error(s, str(exc))

    def reconcile(self):
        positions = self.broker.positions()
        by_id = {p["position_id"]: p for p in positions}
        for s in self.current_signals():
            if s["state"] in {"sending", "uncertain"}:
                action = self.store.action(f"entry:{s['id']}")
                if action and action["state"] == "done":
                    import json
                    self._attach(s, json.loads(action["data"]))
                else:
                    p = next((p for p in positions if p["comment"] == f"GSD:{s['id']}"), None)
                    if p:
                        self._attach(s, p)
                        self.store.finish_action(f"entry:{s['id']}", "done", p)
                continue
            if s["state"] != "open":
                continue
            p = by_id.get(s["position_id"])
            if p:
                s.update(ticket=p["ticket"], volume=p["volume"], sl=p["sl"], tp=p.get("tp", 0), profit=p["profit"])
                self.save(s)
            else:
                # A successful positions query returned no position; verify history before marking closed.
                deals = self.broker.deals(s["position_id"])
                if not deals or not any(d["entry_type"] in (1, 2, 3) for d in deals):
                    continue
                exits = [d for d in deals if d["entry_type"] in (1, 2, 3)]
                last_exit = max(enumerate(exits), key=lambda pair: (pair[1]["time"], pair[0]))[1]
                if s.get("targets") and last_exit.get("reason") == "take_profit":
                    final = s["targets"][-1]
                    tolerance = self.broker.info["point"] / 2
                    # The execution price can slip beyond the trigger; use the
                    # broker's TP reason and the last confirmed TP level instead.
                    matched = abs(s.get("tp", 0) - round(final["price"], self.broker.info["digits"])) < tolerance
                    if matched and final["key"] not in s["completed"]:
                        s["completed"].append(final["key"])
                        self.store.event("نفّذ MT5 الهدف الأخير TP وأغلق المتبقي", s["id"], target=final["price"])
                s.update(state="closed", volume=0., profit=0., closed=max(d["time"] for d in deals), realized=sum(d["net"] for d in deals))
                self.store.save_deals([{**d, "signal": s["id"], "channel": s["channel"], "channel_name": s["channel_name"], "manual": s.get("manual", False)} for d in deals])
                self.save(s)
                self.store.event("الصفقة أُغلقت؛ تمت مطابقة سجل الوسيط", s["id"])
        self._adopt(positions)
        return positions

    def _adopt(self, positions):
        cfg = self.settings
        if cfg.management_scope == "app":
            return
        known = {s.get("position_id") for s in self.current_signals()}
        for p in positions:
            if p["position_id"] in known:
                continue
            if cfg.management_scope == "selected" and p["ticket"] not in cfg.selected_tickets:
                continue
            identifier = hashlib.sha256(f"manual:{self.broker.identity}:{p['position_id']}".encode()).hexdigest()[:16]
            s = {"id": identifier, "root": identifier, "manual": True, "account": self.broker.identity, "channel": 0, "channel_name": "يدوي",
                 "entry": p["entry"], "side": p["side"], "signal_stop": p["sl"], "created": p["time"], "expires": None,
                 "config": cfg.to_dict(), "channel_targets": [], "completed": [], "realized": 0., "secured": False, "be_requested": False}
            self._attach(s, p, sync_target=False)
            if not cfg.manage_manual_stops:
                s["targets"] = []
                s["config"]["breakeven_enabled"] = False
                self.save(s)
            self.store.event("أُضيفت صفقة يدوية إلى المتابعة", s["id"])

    def _manual_enabled(self, s):
        if not s.get("manual"):
            return True
        return self.settings.manage_manual_stops and (self.settings.management_scope == "all" or self.settings.management_scope == "selected" and s["ticket"] in self.settings.selected_tickets)

    def _refresh_manual(self, s, tick):
        cfg = self.settings
        if s.get("manual_retry_at", 0) > self.clock():
            return
        config = cfg.to_dict()
        if s.get("manual_config") == config:
            return
        s["config"] = config
        self._set_targets(s, cfg)
        stop = s.get("sl", 0)
        if cfg.stop_mode == "fixed" or not stop:
            desired = round(s["fill"] + (-1 if s["side"] == "buy" else 1) * cfg.stop_distance, self.broker.info["digits"])
            # Keep an existing stop that already protects more profit.
            improves = not stop or (desired > stop if s["side"] == "buy" else desired < stop)
            if improves:
                if not valid_stop(s["side"], desired, tick["bid"], tick["ask"], self.stop_minimum()):
                    self._entry_error(s, "تعذر تطبيق ستوب الصفقة اليدوية: السعر الحالي أو السبريد خارج مسافة الستوب المحددة")
                    self.save(s)
                    return
                try:
                    self.broker.modify(s["ticket"], desired)
                    s["sl"] = desired
                except BrokerError as exc:
                    s["manual_retry_at"] = self.clock() + 5
                    self._entry_error(s, str(exc))
                    self.save(s)
                    return
        s["manual_config"] = config
        self.save(s)
        self.store.event("فُعّلت إدارة الصفقة اليدوية حسب الإعدادات الحالية", s["id"])

    def manage(self, s, tick):
        if not self._manual_enabled(s):
            return
        if s.get("manual"):
            self._refresh_manual(s, tick)
        self._sync_target(s)
        quote = tick["bid"] if s["side"] == "buy" else tick["ask"]
        cfg = Settings.from_dict(s["config"])
        for index, target in enumerate(s["targets"]):
            key = target["key"]
            if key in s["completed"]:
                continue
            reached = quote >= target["price"] if s["side"] == "buy" else quote <= target["price"]
            if not reached:
                break
            # A final target closes the remainder, never more than the actual position volume.
            volume = s["volume"] if index == len(s["targets"]) - 1 else min(target["lot"], s["volume"])
            if volume <= 0:
                self._entry_error(s, "حدد كمية الإغلاق لهذا الهدف من الإعدادات")
                return
            try:
                info = self.broker.info
                progress = s.setdefault("target_progress", {}).get(key)
                if progress is None:
                    desired = quantize_volume(volume, info["volume_min"], info["volume_max"], info["volume_step"])
                    progress = {"desired": desired, "closed": 0., "attempt": 0}
                    s["target_progress"][key] = progress
                    self.save(s)
                left = round(progress["desired"] - progress["closed"], 8)
                if left <= 1e-8:
                    s["completed"].append(key)
                    self.save(s)
                    continue
                if progress.get("retry_at", 0) > self.clock():
                    return
                quantity = quantize_volume(min(left, s["volume"]), info["volume_min"], info["volume_max"], info["volume_step"])
                remainder = round(s["volume"] - quantity, 8)
                if 0 < remainder < info["volume_min"]:
                    quantity = s["volume"]
                attempt = progress["attempt"]
                action = f"close:{s['id']}:{key}:{attempt}"
                comment = f"GSDC:{s['id']}:{index}:{attempt}"
                old_action = self.store.action(action)
                if old_action:
                    if old_action["state"] == "done":
                        import json
                        self._record_stage_fill(s, key, index, progress, json.loads(old_action["data"])["volume"], cfg, reduce_volume=False)
                        if key not in s["completed"]:
                            return
                        continue
                    if old_action["state"] == "rejected":
                        progress["attempt"] += 1
                        progress["retry_at"] = self.clock() + 5
                        self.save(s)
                        return
                    # Reconcile ambiguous requests using a unique broker comment, never volume alone.
                    ds = self.broker.deals(s["position_id"])
                    fills = [d for d in ds if d["comment"] == comment and d["entry_type"] in (1, 2, 3)]
                    if fills:
                        done = sum(d["volume"] for d in fills)
                        self.store.finish_action(action, "done", {"volume": done})
                        self._record_stage_fill(s, key, index, progress, done, cfg, reduce_volume=False)
                        if key not in s["completed"]:
                            return
                        continue
                    return
                if not self.store.claim(action, s["id"], "close", {"volume": quantity, "ticket": s["ticket"]}):
                    return
                try:
                    result = self.broker.close(s["ticket"], quantity, comment)
                except UncertainExecution as exc:
                    self.store.finish_action(action, "uncertain", {"error": str(exc)})
                    self.store.event(str(exc), s["id"], "error")
                    return
                except BrokerError as exc:
                    self.store.finish_action(action, "rejected", {"error": str(exc)})
                    self._entry_error(s, str(exc))
                    return
                self.store.finish_action(action, "done", result)
                self._record_stage_fill(s, key, index, progress, result["volume"], cfg, reduce_volume=True)
                self.store.event("تم إغلاق جزء عند الهدف", s["id"], stage=index + 1, volume=result["volume"], price=result["price"])
                if s["volume"] < info["volume_min"]:
                    return
                if key not in s["completed"]:
                    return
            except BrokerError as exc:
                self._entry_error(s, str(exc))
                return
        if s["be_requested"]:
            self._secure(s)

    def _record_stage_fill(self, s, key, index, progress, volume, cfg, reduce_volume):
        progress["closed"] = round(progress["closed"] + volume, 8)
        progress["attempt"] += 1
        if reduce_volume:
            before = s["volume"]
            s["volume"] = round(s["volume"] - volume, 8)
            s["profit"] = s.get("profit", 0) * s["volume"] / before if before else 0
        self.needs_reconcile = True
        self.last_history = 0
        if progress["closed"] + 1e-8 >= progress["desired"] and key not in s["completed"]:
            s["completed"].append(key)
        if volume > 0 and cfg.breakeven_enabled and index + 1 >= cfg.breakeven_stage:
            s["be_requested"] = True
        self.save(s)
        if s["be_requested"] and s["volume"] >= self.broker.info["volume_min"]:
            self._secure(s)

    def sync_history(self, budgeted=False):
        signals = [s for s in self.current_signals() if s.get("position_id") and s["state"] in {"open", "closed"}]
        if budgeted and signals:
            # Archive size must not create an unbounded pause in Telegram reception.
            total = len(signals)
            signals = [signals[(self.history_cursor + i) % total] for i in range(min(3, total))]
            self.history_cursor = (self.history_cursor + len(signals)) % total
        for s in signals:
            if s.get("position_id") and s["state"] in {"open", "closed"}:
                deals = self.broker.deals(s["position_id"])
                tagged = [{**d, "signal": s["id"], "channel": s["channel"], "channel_name": s["channel_name"], "manual": s.get("manual", False)} for d in deals]
                self.store.save_deals(tagged)
                s["realized"] = sum(d["net"] for d in deals)
                self.save(s)

    def step(self):
        self.expire_pending()
        self.reconcile()
        # Reading positions/history does not require a fresh execution quote.
        # Manual closures must still disappear while the market feed is stale.
        try:
            tick = self.broker.tick()
        except AccountChanged:
            raise
        except BrokerError:
            if self.clock() - self.last_history >= 5:
                self.sync_history(budgeted=True)
                self.last_history = self.clock()
            raise
        for s in list(self.current_signals()):
            if s["state"] == "open":
                self.manage(s, tick)
        for s in list(self.current_signals()):
            if s["state"] == "pending":
                self._try_entry(s, tick)
        if self.needs_reconcile:
            self.reconcile()
            self.needs_reconcile = False
        if self.clock() - self.last_history >= 5:
            self.sync_history(budgeted=True)
            self.last_history = self.clock()

    def expire_pending(self):
        for s in self.current_signals():
            if s["state"] == "pending" and s["expires"] is not None and self.clock() >= s["expires"]:
                s["state"] = "expired"
                self.save(s)
                self.store.event("انتهت مهلة انتظار الإشارة", s["id"])

    def cancel(self, identifier):
        s = self.signals.get(identifier)
        if s and s["state"] == "pending":
            s["state"] = "cancelled"
            self.save(s)
            self.store.event("أُلغيت الإشارة يدويًا", identifier)

    def apply_settings(self, settings):
        settings.validate()
        self.settings = settings
        self.store.set("settings", settings.to_dict())
        if settings.channel_id:
            self.store.set(f"profile:{settings.channel_id}", settings.to_dict())
        self.store.event("حُفظت الإعدادات؛ إعدادات التنفيذ الجديدة للإشارات التالية")

    def snapshot(self):
        signals = sorted(self.current_signals(), key=lambda s: s["created"], reverse=True)
        return {"mode": self.broker.mode, "paused": self.paused, "account": self.broker.account(), "tick": self.broker.tick(),
                "symbol": self.broker.symbol, "signals": signals, "events": self.store.events(), "unresolved": len(self.store.unresolved()),
                "settings": self.settings.to_dict()}
