"""Measure local processing only; all orders go to an isolated demo broker."""
import json
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
    print(json.dumps({"parser": stats(parse_times), "receipt_to_simulated_execution": stats(callback_times),
                      "note": "Local simulation timings exclude Telegram network and broker latency."}))
finally:
    service.store.close()
    service.pool.shutdown()
