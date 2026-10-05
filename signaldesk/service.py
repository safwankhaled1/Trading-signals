from __future__ import annotations

import asyncio
import json
import os
import queue
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from multiprocessing.connection import Listener

from . import __version__, ENGINE_PROTOCOL
from .broker import AccountChanged, BrokerError, DemoBroker, MT5Broker, discover_terminals, symbol_catalog
from .config import Settings, data_directory
from .engine import TradingEngine
from .reports import build_report, export_report
from .security import save_secret
from .store import Store
from .telegram_client import TelegramGateway


class Service:
    def __init__(self, mode):
        self.mode = mode
        self.directory = data_directory()
        self.store = Store(self.directory / f"{mode}.sqlite")
        if self.store.get("last_account"):
            self.store.activate_account(self.store.get("last_account"))
        self.settings = Settings.from_dict(self.store.get("settings", {}))
        self.engine = None
        self.broker = None
        self.commands = queue.Queue()
        self.running = True
        self.cache = {"mode": mode, "connected": False, "settings": self.settings.to_dict(), "signals": [], "events": [], "paused": True,
                      "account": {}, "tick": {}, "symbol": "", "channels": [], "terminals": [], "symbols": [], "telegram": "غير متصل", "report": {}, "notice": ""}
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="reports")
        self.gateway = TelegramGateway(self.directory, self.on_message, self.telegram_status)
        self.report_future = None
        self.report_account = None
        self.channel_sync_required = False
        self.notice = ""
        self.notice_id = 0
        self.terminals = []
        self.symbols = []
        self.mt5_error = ""
        self.deferred = self.store.get("inbox", [])
        self.started = time.time()

    def telegram_status(self, state):
        self.notice = state
        self.notice_id += 1

    def on_message(self, channel, message_id, raw, reply, edited, received, channel_name):
        if edited and not self.store.message(channel, message_id):
            return
        self.store.event("وصل تعديل رسالة من القناة" if edited else "وصلت رسالة جديدة من القناة", message_id=message_id, channel=channel)
        self.deferred.append([channel, message_id, raw, reply, edited, received, channel_name])
        self.store.set("inbox", self.deferred)
        self.drain_inbox()

    def drain_inbox(self):
        if not self.engine or not self.deferred:
            return
        try:
            tick = self.broker.tick()
        except BrokerError:
            return
        for _ in range(min(10, len(self.deferred))):
            channel, mid, raw, reply, edited, received, name = self.deferred[0]
            cfg = self.engine.settings_for(channel)
            if not edited and cfg.expiry_enabled and time.time() >= received + cfg.wait_minutes * 60:
                self.store.event("انتهت مهلة رسالة منتظرة أثناء انقطاع MT5", level="warning", message_id=mid)
            else:
                self.engine.receive(channel, mid, raw, reply, edited, received, name, tick=tick)
            self.deferred.pop(0)
            self.store.set("inbox", self.deferred)

    def validate_connection_change(self, settings):
        if self.broker and self.mode == "live" and self.engine:
            try:
                self.broker.account()
            except AccountChanged:
                return
            except BrokerError:
                pass
            changed_path = bool(settings.terminal_path and settings.terminal_path != self.broker.path)
            changed_symbol = bool(settings.symbol and settings.symbol != self.broker.symbol)
            active = any(s["state"] in {"pending", "open", "sending", "uncertain"} for s in self.engine.current_signals())
            if active and (changed_path or changed_symbol):
                raise BrokerError("أغلق الصفقات وألغِ الإشارات المنتظرة قبل تغيير المنصة أو رمز التداول")

    def activate_account(self, identity, symbol_override=None):
        previous = self.store.account
        terminal = self.settings.terminal_path
        self.store.activate_account(identity)
        if previous != identity:
            self.settings = Settings.from_dict(self.store.get("settings", {}))
            self.settings.terminal_path = terminal
            self.deferred = self.store.get("inbox", [])
            self.cache.update(account_id=identity, settings=self.settings.to_dict(), signals=[], events=[], report={},
                              account={}, tick={}, symbol="", unresolved=0, connected=False, quote_ready=False, paused=True)
            self.symbols = []
            if self.report_future:
                self.report_future.cancel()
                self.report_future = None
            self.gateway.set_channel(self.settings.channel_id, self.settings.channel_name)
            self.channel_sync_required = True
            if self.mode == "live":
                self.store.set("paused", True)
        if symbol_override is not None:
            self.settings.symbol = symbol_override
        self.store.set("last_account", identity)
        self.store.set("settings", self.settings.to_dict())
        return self.settings.symbol

    def connect_broker(self, symbol_override=None):
        self.validate_connection_change(self.settings)
        if self.broker:
            self.broker.shutdown()
        self.broker, self.engine = None, None
        try:
            if self.mode == "demo":
                self.broker = DemoBroker(self.store)
                self.activate_account(self.broker.identity)
            else:
                self.broker = MT5Broker(self.settings.terminal_path, self.settings.symbol,
                                        account_settings=lambda identity: self.activate_account(identity, symbol_override))
            self.settings.symbol = self.broker.symbol
            self.store.set("settings", self.settings.to_dict())
            self.engine = TradingEngine(self.store, self.broker, self.settings)
            if self.mode == "live" and self.store.get("paused") is None:
                self.engine.paused = True
                self.store.set("paused", True)
            self.store.set("last_account", self.broker.identity)
            self.store.event("اتصل محرك التداول", mode=self.mode, account=self.broker.identity)
            self.mt5_error = ""
            self.notice = ("تم الاتصال بـMT5 • " + self.broker.symbol) if self.mode == "live" else "تم تشغيل المحاكاة"
            self.notice_id += 1
            if self.mode == "live":
                self.symbols = self.broker.symbols()
        except Exception as exc:
            self.mt5_error = str(exc)
            self.store.event(str(exc), level="error")
            self.notice = str(exc)
            self.notice_id += 1

    def listen(self):
        auth = secrets.token_bytes(32)
        self.listener = Listener(("127.0.0.1", 0), authkey=auth)
        save_secret(self.directory / f"{self.mode}.runtime", {"port": self.listener.address[1], "key": auth.hex(), "pid": os.getpid()})
        def client(connection):
            try:
                while self.running:
                    command = connection.recv()
                    if command.get("action") != "snapshot":
                        self.commands.put(command)
                    connection.send(self.cache)
            except (EOFError, OSError, ConnectionResetError):
                pass
            finally:
                connection.close()
        def accept():
            while self.running:
                try:
                    connection = self.listener.accept()
                    threading.Thread(target=client, args=(connection,), daemon=True).start()
                except Exception:
                    if not self.running:
                        break
        threading.Thread(target=accept, daemon=True).start()

    async def telegram_command(self, action, cmd):
        self.gateway.busy = True
        self.gateway.error = ""
        try:
            if action == "telegram_connect":
                await self.gateway.connect(cmd.get("api_id"), cmd.get("api_hash"))
            elif action == "telegram_code":
                await self.gateway.send_code(cmd["phone"])
            elif action == "telegram_login":
                await self.gateway.sign_in(cmd.get("code", ""), cmd.get("password", ""))
            else:
                await self.gateway.logout()
            self.store.event("تلجرام: " + self.gateway.state)
        except Exception as exc:
            messages = {"ApiIdInvalidError": "API ID أو API Hash غير صحيح",
                        "PhoneNumberInvalidError": "رقم الهاتف غير صحيح؛ أدخله مع رمز البلد",
                        "PhoneCodeInvalidError": "رمز التحقق غير صحيح",
                        "PhoneCodeExpiredError": "انتهت صلاحية رمز التحقق؛ اطلب رمزًا جديدًا",
                        "PasswordHashInvalidError": "كلمة مرور التحقق بخطوتين غير صحيحة"}
            reason = messages.get(type(exc).__name__, str(exc) or "تعذر الاتصال بتلجرام؛ تحقق من اتصال الإنترنت")
            if type(exc).__name__ == "FloodWaitError":
                reason = f"تلجرام يطلب الانتظار {getattr(exc, 'seconds', 0)} ثانية قبل إعادة المحاولة"
            self.gateway.error = reason
            if not self.gateway.authorized:
                self.gateway.state = "فشل الاتصال أو تسجيل الدخول"
            raise ValueError(reason) from exc
        finally:
            self.gateway.busy = False

    async def command(self, cmd):
        action = cmd.get("action")
        if cmd.get("account_id") and cmd["account_id"] != self.store.account and action not in {"stop", "discover", "telegram_connect", "telegram_code", "telegram_login", "telegram_logout"}:
            raise ValueError("تغيّر الحساب؛ راجع الحساب الحالي ثم أعد تنفيذ الإجراء")
        if action == "stop":
            self.running = False
        elif action == "connect_mt5":
            settings = Settings.from_dict({**self.settings.to_dict(),
                                          "terminal_path": cmd.get("path", self.settings.terminal_path),
                                          "symbol": cmd.get("symbol", self.settings.symbol)})
            self.validate_connection_change(settings)
            self.settings = settings
            self.store.set("settings", self.settings.to_dict())
            self.connect_broker(symbol_override=cmd.get("symbol"))
        elif action == "discover":
            self.terminals = discover_terminals()
            # Reading available symbols needs an initialized terminal, but sends no trade.
            try:
                import MetaTrader5 as mt5
                path = cmd.get("path", "")
                if self.broker and self.mode == "live":
                    self.symbols = self.broker.symbols()
                elif path or len(self.terminals) == 1:
                    try:
                        if not mt5.initialize(path or self.terminals[0], timeout=5000):
                            raise BrokerError("تعذر اكتشاف رموز MT5: " + str(mt5.last_error()))
                        self.symbols = symbol_catalog(mt5.symbols_get())
                    finally:
                        mt5.shutdown()
            except ImportError:
                pass
        elif action == "settings":
            settings = Settings.from_dict(cmd["settings"])
            self.validate_connection_change(settings)
            reconnect = bool(self.mode == "live" and self.broker and
                             ((settings.symbol and settings.symbol != self.broker.symbol) or
                              (settings.terminal_path and settings.terminal_path != self.broker.path)))
            old_channel = self.settings.channel_id
            if old_channel != settings.channel_id and cmd.get("cancel_previous", False):
                self.deferred = [item for item in self.deferred if item[0] != old_channel]
                self.store.set("inbox", self.deferred)
                if self.engine:
                    for s in self.engine.current_signals():
                        if s["channel"] == old_channel:
                            self.engine.cancel(s["id"])
            self.settings = settings
            if self.engine:
                self.engine.apply_settings(settings)
            else:
                self.store.set("settings", settings.to_dict())
                if settings.channel_id:
                    self.store.set(f"profile:{settings.channel_id}", settings.to_dict())
            if reconnect:
                self.connect_broker(symbol_override=settings.symbol)
                settings = self.settings
            if settings.channel_id != self.gateway.channel_id or (settings.channel_id and self.gateway.authorized and not self.gateway.baseline_ready):
                await self.gateway.select_channel(settings.channel_id, settings.channel_name)
            self.notice = "تم حفظ الإعدادات"
            self.notice_id += 1
        elif action in {"load_profile", "choose_channel"}:
            identifier = int(cmd["id"])
            base = Settings.from_dict(cmd.get("settings", self.settings.to_dict()))
            if self.settings.channel_id:
                previous = base.to_dict()
                previous.update(channel_id=self.settings.channel_id, channel_name=self.settings.channel_name)
                self.store.set(f"profile:{self.settings.channel_id}", previous)
            data = self.store.get(f"profile:{identifier}", base.to_dict())
            # Connection and account-management scope belong to this installation,
            # not to an archived channel's profile.
            for key in ("terminal_path", "symbol", "management_scope", "selected_tickets", "manage_manual_stops", "report_timezone", "report_utc_offset"):
                data[key] = getattr(base, key)
            data.update(channel_id=identifier, channel_name=cmd["name"])
            await self.command({"action": "settings", "settings": data, "cancel_previous": cmd.get("cancel_previous", False)})
        elif action == "pause" and self.engine:
            if not cmd["paused"] and self.mode == "live":
                account = self.broker.account()
                if not account["hedging"] or not account["trade_allowed"]:
                    raise BrokerError("التنفيذ يحتاج حساب Hedging والتداول الآلي مفعّلًا")
            self.engine.paused = bool(cmd["paused"])
            self.store.set("paused", self.engine.paused)
            self.store.event("إيقاف الدخول الجديد؛ إدارة الصفقات مستمرة" if self.engine.paused else "تم تفعيل الدخول الجديد")
            self.notice = "تم إيقاف الدخول الجديد" if self.engine.paused else "تم تفعيل الدخول على " + self.broker.symbol
            self.notice_id += 1
        elif action == "pause":
            raise BrokerError(self.mt5_error or "اربط حساب MT5 أولًا من صفحة الاتصالات")
        elif action == "cancel" and self.engine:
            self.engine.cancel(cmd["id"])
        elif action == "demo_price" and self.mode == "demo" and self.broker:
            self.broker.set_price(float(cmd["price"]))
        elif action == "demo_signal" and self.mode == "demo" and self.engine:
            identifier = int(time.time_ns() // 1000)
            self.engine.receive(self.settings.channel_id or -1001, identifier, cmd["text"], cmd.get("reply"), cmd.get("edited", False), channel_name=self.settings.channel_name or "قناة المحاكاة")
        elif action in {"telegram_connect", "telegram_code", "telegram_login", "telegram_logout"}:
            await self.telegram_command(action, cmd)
        elif action == "sync_channel":
            await self.gateway.select_channel(self.settings.channel_id, self.settings.channel_name)
        elif action == "report":
            if not self.store.account:
                raise ValueError("اربط حسابًا أولًا لعرض تقريره")
            if self.report_future and not self.report_future.done():
                raise ValueError("التقرير السابق قيد التحضير")
            cfg = self.settings
            self.report_account = self.store.account
            self.report_future = self.pool.submit(build_report, self.store.path, cmd["start"], cmd["end"], cmd.get("channel"), cmd.get("scope", "all"), cfg.report_utc_offset, cfg.report_timezone == "local", self.report_account)
        elif action == "export":
            report = self.cache.get("report", {})
            if not report.get("start"):
                raise ValueError("جهّز تقريرًا أولًا")
            result = await asyncio.get_running_loop().run_in_executor(self.pool, export_report, report, cmd["path"])
            self.notice = "تم تصدير التقرير: " + result
            self.notice_id += 1

    def snapshot(self):
        snapshot = dict(self.cache)
        # Configuration and channel confirmation must survive a missing/stale quote.
        snapshot.update(settings=self.settings.to_dict(), events=self.store.events() if self.store.account else [], version=__version__, engine_protocol=ENGINE_PROTOCOL,
                        account_id=self.store.account or "", signals=[], unresolved=0, paused=True, symbol="",
                        connected=False, quote_ready=False, account={}, tick={}, mt5_error=self.mt5_error, quote_error="")
        if self.engine:
            snapshot.update(signals=sorted(self.engine.current_signals(), key=lambda s: s["created"], reverse=True),
                            paused=self.engine.paused, unresolved=len(self.store.unresolved()), symbol=self.broker.symbol)
            try:
                snapshot.update(account=self.broker.account(), connected=True, mt5_error="")
                try:
                    quote = self.broker.tick()
                    snapshot.update(tick=quote, quote_ready=True,
                                    spread=round(quote["ask"] - quote["bid"], 8),
                                    stop_minimum=self.engine.stop_minimum(), digits=self.broker.info["digits"])
                except BrokerError as exc:
                    snapshot["quote_error"] = str(exc)
            except BrokerError as exc:
                snapshot["mt5_error"] = str(exc)
                if isinstance(exc, AccountChanged):
                    snapshot.update(signals=[], events=[], report={}, symbol="", unresolved=0, paused=True)
        tg_connected = bool(self.gateway.authorized and self.gateway.client and self.gateway.client.is_connected())
        tg_state = self.gateway.state
        tg_error = self.gateway.error
        if self.gateway.authorized and not tg_connected:
            tg_state = "انقطع الاتصال"
            tg_error = tg_error or "انقطع اتصال تلجرام؛ جارٍ انتظار إعادة الاتصال"
        snapshot.update(telegram=tg_state, telegram_connected=tg_connected, telegram_error=tg_error,
                        telegram_busy=self.gateway.busy, mt5_state="تم الاتصال" if snapshot["connected"] else "غير متصل",
                        channel_listening=tg_connected and self.gateway.baseline_ready,
                        channels=self.gateway.channels, terminals=self.terminals, symbols=self.symbols, notice=self.notice, notice_id=self.notice_id,
                        heartbeat=time.time(), report_busy=bool(self.report_future and not self.report_future.done()))
        self.cache = snapshot

    async def run(self):
        self.connect_broker()
        self.listen()
        await self.gateway.select_channel(self.settings.channel_id, self.settings.channel_name)
        last_snapshot = 0
        last_error = ""
        last_connection_attempt = time.time()
        tasks = set()
        async def background_command(cmd):
            try:
                await self.command(cmd)
            except Exception as exc:
                self.notice = str(exc)
                self.notice_id += 1
                self.store.event(str(exc), level="warning", action=cmd.get("action"))
        if self.gateway.path.exists():
            task = asyncio.create_task(background_command({"action": "telegram_connect"}))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
        while self.running:
            if not self.engine and self.mode == "live" and time.time() - last_connection_attempt >= 15:
                self.connect_broker()
                last_connection_attempt = time.time()
            if self.channel_sync_required and self.gateway.authorized:
                self.channel_sync_required = False
                task = asyncio.create_task(background_command({"action": "sync_channel"}))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
            self.drain_inbox()
            # Positions and pending entries are handled before rendering/reporting commands.
            if self.engine:
                try:
                    self.engine.step()
                    last_error = ""
                except AccountChanged:
                    self.connect_broker()
                except Exception as exc:
                    error = str(exc)
                    if error != last_error:
                        self.store.event(error, level="error")
                        self.notice, last_error = error, error
                        self.notice_id += 1
            for _ in range(10):
                try:
                    cmd = self.commands.get_nowait()
                except queue.Empty:
                    break
                try:
                    if cmd.get("action") in {"telegram_connect", "telegram_code", "telegram_login", "telegram_logout", "export"}:
                        task = asyncio.create_task(background_command(cmd))
                        tasks.add(task)
                        task.add_done_callback(tasks.discard)
                    else:
                        await self.command(cmd)
                        if not self.running:
                            break
                except Exception as exc:
                    self.notice = str(exc)
                    self.notice_id += 1
                    self.store.event(str(exc), level="warning", action=cmd.get("action"))
            if self.report_future and self.report_future.done():
                try:
                    report = self.report_future.result()
                    if self.report_account == self.store.account:
                        self.cache = {**self.cache, "report": report}
                except Exception as exc:
                    self.notice = str(exc)
                    self.notice_id += 1
                self.report_future = None
            if time.time() - last_snapshot >= .25:
                self.snapshot()
                last_snapshot = time.time()
            await asyncio.sleep(.03)
        await self.gateway.disconnect()
        for task in tasks:
            task.cancel()
        if self.broker:
            self.broker.shutdown()
        self.pool.shutdown(wait=False)
        self.listener.close()
        self.store.close()


def run_service(mode):
    import msvcrt
    directory = data_directory()
    with (directory / f"{mode}.lock").open("a+b") as lock:
        lock.seek(0)
        if lock.read(1) == b"":
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return
        try:
            asyncio.run(Service(mode).run())
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
