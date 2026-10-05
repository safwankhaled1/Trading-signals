"""Measure local processing only; all orders go to an isolated demo broker."""
import json
import asyncio
import os
from pathlib import Path
import statistics
import sys
import time

project = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project))
os.environ["SIGNALDESK_DATA_DIR"] = str(project / ".local-data" / f"benchmark-{time.time_ns()}")

from signaldesk.parser import parse_message
from signaldesk.service import Service
from signaldesk.timing import high_resolution_timer

texts = ["كرر شراء الان من 60\nستوب 59.5", "شرا دهب حالياً 60 ستبو59,5", "متاحه 🔥"]
parse_times = []
for _ in range(1000):
    for text in texts:
        started = time.perf_counter()
        parse_message(text)
        parse_times.append((time.perf_counter() - started) * 1000)
service = Service("demo")
try:
    service.connect_broker()
    service.broker.spread = 0
    service.broker.set_price(4160)
    callback_times = []
    for mid in range(1, 21):
        started = time.perf_counter()
        service.on_message(-1001, mid, "شراء الان من 60\nستوب 59.5", None, False, time.time(), "محاكاة")
        callback_times.append((time.perf_counter() - started) * 1000)
    assert len(service.broker.positions()) == 20
    def stats(values):
        return {"median_ms": round(statistics.median(values), 3), "p95_ms": round(sorted(values)[int(len(values) * .95) - 1], 3)}
    async def measure_polls():
        service.broker.open_positions.clear()
        service.engine.signals.clear()
        service.broker.bid = 4160
        service.engine.receive(-1002, 1, "شراء 4164 ستوب 4163.5")
        price_changed = None
        order_requested = None
        original_open = service.broker.open
        def timed_open(*args):
            nonlocal order_requested
            order_requested = time.perf_counter()
            return original_open(*args)
        service.broker.open = timed_open
        async def price_feed():
            nonlocal price_changed
            await asyncio.sleep(.02)
            service.broker.bid = 4164
            price_changed = time.perf_counter()
        feed = asyncio.create_task(price_feed())
        while order_requested is None:
            service.engine.step(include_history=False)
            await service.wait_for_work()
        await feed
        service.broker.open = original_open
        intervals = []
        started = previous = time.perf_counter()
        cpu_started = time.process_time()
        while time.perf_counter() - started < 2:
            service.engine.step(include_history=False)
            await service.update_archive()
            await service.wait_for_work()
            now = time.perf_counter()
            intervals.append((now - previous) * 1000)
            previous = now
        return {"active_poll": stats(intervals), "polls_per_second": round(len(intervals) / (now - started), 1),
                "cpu_percent_of_one_core": round((time.process_time() - cpu_started) / (now - started) * 100, 1),
                "pending_price_to_order_request_ms": round((order_requested - price_changed) * 1000, 3)}
    with high_resolution_timer() as precise_timer:
        polling = asyncio.run(measure_polls())
    print(json.dumps({"parser": stats(parse_times), "receipt_to_simulated_execution": stats(callback_times),
                      **polling, "high_resolution_timer": precise_timer,
                      "note": "Local simulation timings exclude Telegram network and broker latency; OS scheduling can exceed the requested 1ms interval."}))
finally:
    service.store.close()
    service.pool.shutdown()
