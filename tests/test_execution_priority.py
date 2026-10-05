import asyncio
import threading

import pytest

from signaldesk.broker import BrokerError
from signaldesk.service import Service


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNALDESK_DATA_DIR", str(tmp_path))
    worker = Service("demo")
    worker.connect_broker()
    worker.broker.spread = 0
    worker.broker.set_price(4160)
    yield worker
    worker.store.close()
    worker.pool.shutdown()


def test_waiting_entry_executes_without_historical_archive(service, monkeypatch):
    engine = service.engine
    identifier = engine.receive(-1001, 1, "شراء 4164 ستوب 4163.5")
    assert engine.signals[identifier]["state"] == "pending"
    engine.signals["old"] = dict(id="old", account=service.broker.identity,
        symbol=service.broker.symbol, state="closed", position_id=7)
    monkeypatch.setattr(service.broker, "deals", lambda pid: pytest.fail("Archive blocked entry"))
    service.broker.set_price(4164)
    engine.step(include_history=False)
    assert engine.signals[identifier]["state"] == "open"


def test_arriving_telegram_callback_runs_before_archive(service, monkeypatch):
    order = []
    original_open = service.broker.open
    original_event = service.store.event
    def open_trade(*args):
        trade = next(iter(service.engine.signals.values()))
        assert service.store.action(f"entry:{trade['id']}")["state"] == "sending"
        order.append("execution")
        return original_open(*args)
    def event(message, *args, **kwargs):
        if message in {"استُقبلت إشارة دخول", "وصلت رسالة جديدة من القناة"}:
            order.append("activity_log")
        return original_event(message, *args, **kwargs)
    monkeypatch.setattr(service.broker, "open", open_trade)
    monkeypatch.setattr(service.store, "event", event)
    monkeypatch.setattr(service.engine, "archive_step", lambda: order.append("archive"))
    async def exercise():
        asyncio.get_running_loop().call_soon(service.on_message, -1001, 1,
            "شراء 4160 ستوب 4159.5", None, False, service.engine.clock(), "اختبار")
        await service.update_archive()
    asyncio.run(exercise())
    assert order == ["execution", "activity_log", "activity_log", "archive"]


def test_queued_command_defers_archive(service, monkeypatch):
    monkeypatch.setattr(service.engine, "archive_step", lambda: pytest.fail("Pending command blocked"))
    service.commands.put({"action": "pause", "paused": True})
    assert asyncio.run(service.update_archive()) is False


def test_command_from_ipc_thread_wakes_idle_wait_immediately(service, monkeypatch):
    # Disable the timeout: the only way to finish is the event from the IPC thread.
    async def no_timeout(awaitable, timeout):
        return await awaitable
    monkeypatch.setattr(asyncio, "wait_for", no_timeout)
    async def exercise():
        service.loop = asyncio.get_running_loop()
        waiter = asyncio.create_task(service.wait_for_work())
        await asyncio.sleep(0)
        thread = threading.Thread(target=lambda: service.loop.call_soon_threadsafe(service.wakeup.set))
        thread.start()
        await waiter
        thread.join()
        assert not service.wakeup.is_set()
    asyncio.run(exercise())


def test_active_poll_uses_one_millisecond_and_idle_does_not_spin(service, monkeypatch):
    intervals = []
    async def record_timeout(awaitable, timeout):
        intervals.append(timeout)
        awaitable.close()
        raise asyncio.TimeoutError
    monkeypatch.setattr(asyncio, "wait_for", record_timeout)
    asyncio.run(service.wait_for_work())
    service.engine.receive(-1001, 1, "شراء 4164 ستوب 4163.5")
    asyncio.run(service.wait_for_work())
    assert intervals == [.05, .001]


def test_floating_profit_does_not_rewrite_trade_but_sl_changes_persist(service):
    engine = service.engine
    identifier = engine.receive(-1001, 1, "شراء 4160 ستوب 4159.5")
    before = service.store.db.total_changes
    service.broker.bid = 4161  # Price only; do not write the demo broker's saved state.
    for _ in range(100):
        engine.step(include_history=False)
    assert engine.signals[identifier]["profit"] == 3
    assert service.store.db.total_changes == before
    service.broker.open_positions[0]["sl"] = 4160.1
    engine.step(include_history=False)
    assert next(s for s in service.store.signals() if s["id"] == identifier)["sl"] == 4160.1


def test_archive_reads_one_record_then_yields_and_visits_every_trade(service, monkeypatch):
    engine = service.engine
    now = [1800000000.]
    engine.clock = lambda: now[0]
    for i in range(60):
        engine.signals[str(i)] = dict(id=str(i), account=service.broker.identity, symbol=service.broker.symbol,
            state="closed", position_id=i + 1, channel=0, channel_name="archive")
    calls = []
    monkeypatch.setattr(service.broker, "deals", lambda pid: calls.append(pid) or [])
    for _ in range(60):
        assert engine.archive_step()
        assert not engine.archive_step()
        now[0] += 1
    assert calls == list(range(1, 61))


def test_failed_archive_is_rate_limited_and_does_not_block_pending_execution(service, monkeypatch):
    engine = service.engine
    now = [1800000000.]
    engine.clock = lambda: now[0]
    engine.signals["old"] = dict(id="old", account=service.broker.identity, symbol=service.broker.symbol,
        state="closed", position_id=7, channel=0, channel_name="archive")
    def unavailable(pid):
        raise BrokerError("History unavailable")
    monkeypatch.setattr(service.broker, "deals", unavailable)
    with pytest.raises(BrokerError):
        engine.archive_step()
    assert not engine.archive_step()
    identifier = engine.receive(-1001, 1, "شراء 4164 ستوب 4163.5")
    service.broker.set_price(4164)
    engine.step(include_history=False)
    assert engine.signals[identifier]["state"] == "open"


def test_unchanged_deals_do_not_rewrite_archive(service):
    deal = {"id": "test:1", "net": 5, "account": service.broker.identity}
    service.store.save_deals([deal])
    before = service.store.db.total_changes
    service.store.save_deals([deal])
    assert service.store.db.total_changes == before
    service.store.save_deals([{**deal, "net": 6}])
    assert service.store.deals() == [{**deal, "net": 6}]
    assert service.store.db.total_changes == before + 1
