"""Exercise the real independent service and authenticated UI channel without MT5."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from multiprocessing.connection import Client
from signaldesk.security import load_secret
from signaldesk import __version__, ENGINE_PROTOCOL


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable")
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    directory = project / ".local-data" / f"smoke-{time.time_ns()}"
    directory.mkdir(parents=True)
    environment = {**os.environ, "SIGNALDESK_DATA_DIR": str(directory)}
    log = (directory / "service.log").open("w", encoding="utf-8")
    command = [args.executable, "--engine"] if args.executable else [sys.executable, str(project / "main.py"), "--engine"]
    process = subprocess.Popen(command, env=environment,
                               creationflags=subprocess.CREATE_NO_WINDOW, stdout=log, stderr=log)
    connection = None
    try:
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError((directory / "service.log").read_text(encoding="utf-8"))
            if (directory / "demo.runtime").exists():
                runtime = load_secret(directory / "demo.runtime")
                connection = Client(("127.0.0.1", runtime["port"]), authkey=bytes.fromhex(runtime["key"]))
                break
            time.sleep(.1)
        if connection is None:
            raise RuntimeError("Service startup timed out")
        def request(action="snapshot", **values):
            connection.send({"action": action, **values})
            assert connection.poll(5), "Service did not answer"
            return connection.recv()
        def wait_for(predicate):
            for _ in range(60):
                snapshot = request()
                if predicate(snapshot):
                    return snapshot
                time.sleep(.1)
            raise AssertionError(json.dumps(snapshot, ensure_ascii=True))
        initial = wait_for(lambda s:s.get("connected"))
        assert initial["version"] == __version__
        assert initial["engine_protocol"] == ENGINE_PROTOCOL
        assert initial["quote_ready"] and not initial["quote_error"]
        for channel_id, channel_name in [(-1001001, "قناة الاختبار الأولى"), (-1001002, "قناة الاختبار الثانية")]:
            request("choose_channel", id=channel_id, name=channel_name)
            confirmed = wait_for(lambda s:s["settings"]["channel_id"] == channel_id)
            assert confirmed["settings"]["channel_name"] == channel_name
        request("demo_price", price=4164)
        wait_for(lambda s:s.get("tick", {}).get("bid") == 4164)
        request("demo_signal", text="كرر شراء ذهب الان 64\n\nستوب 63.5")
        opened = wait_for(lambda s:any(t["state"] == "open" for t in s["signals"]))
        trade = opened["signals"][0]
        assert trade["volume"] == .03
        assert trade["entry"] == 4164 and trade["sl"] == 4163.5
        assert trade["tp"] == trade["targets"][-1]["price"]
        # Disconnect the desktop client; the engine must continue running alone.
        connection.close()
        time.sleep(.4)
        connection = Client(("127.0.0.1", runtime["port"]), authkey=bytes.fromhex(runtime["key"]))
        request("demo_price", price=trade["fill"] + 5)
        managed = wait_for(lambda s:any(t["secured"] and abs(t["volume"]-.02)<1e-8 for t in s["signals"]))
        assert len(managed["signals"]) == 1
        (project / ".video-review" / "service-snapshot.json").write_text(json.dumps(managed, ensure_ascii=False), encoding="utf-8")
        request("stop")
        process.wait(timeout=10)
        # Restart from the same persisted state, including completed partial closes.
        process = subprocess.Popen(command, env=environment,
                                   creationflags=subprocess.CREATE_NO_WINDOW, stdout=log, stderr=log)
        connection.close()
        connection = None
        time.sleep(1)
        for _ in range(100):
            try:
                runtime = load_secret(directory / "demo.runtime")
                connection = Client(("127.0.0.1", runtime["port"]), authkey=bytes.fromhex(runtime["key"]))
                break
            except (OSError, EOFError):
                time.sleep(.1)
        restored = wait_for(lambda s:any(t.get("secured") for t in s.get("signals",[])))
        assert restored["signals"][0]["volume"] == .02
        assert restored["signals"][0]["completed"] == ["stage:0"]
        assert restored["signals"][0]["tp"] == restored["signals"][0]["targets"][-1]["price"]
        assert restored["settings"]["channel_id"] == -1001002
        # Exercise the omitted-instrument format from the user's actual channel.
        request("demo_price", price=4160)
        wait_for(lambda s:all(t["state"] != "open" for t in s["signals"]))
        request("demo_signal", text="كرر شراء الان من 60\n\nستوب 59.5")
        final = wait_for(lambda s:any(t["state"] == "open" and t["entry"] == 4160 for t in s["signals"]))
        latest = next(t for t in final["signals"] if t["state"] == "open")
        assert latest["sl"] == 4159.5
        request("demo_signal", text="متاحه مجددا 🔥", reply=latest["message"])
        final = wait_for(lambda s:any("لم يتم التكرار" in e["message"] for e in s["events"]))
        assert sum(t["state"] == "open" for t in final["signals"]) == 1
        # The optional direct mode bypasses the margin and keeps the exact channel SL.
        request("demo_price", price=4150)
        closed = wait_for(lambda s:all(t["state"] != "open" for t in s["signals"]))
        direct_cfg = {**closed["settings"], "entry_mode":"direct", "fixed_lot":.04}
        request("settings", settings=direct_cfg)
        wait_for(lambda s:s["settings"].get("entry_mode") == "direct")
        request("demo_price", price=4117.1)
        wait_for(lambda s:s.get("tick",{}).get("bid") == 4117.1)
        request("demo_signal", text="اشتري ذهب الان من 15\n\nستوب 14.5")
        direct = wait_for(lambda s:any(t["state"] == "open" and t["entry"] == 4115 for t in s["signals"]))
        direct_trade = next(t for t in direct["signals"] if t["state"] == "open")
        assert direct_trade["fill"] == 4117.3 and direct_trade["sl"] == 4114.5 and direct_trade["volume"] == .04
        request("demo_signal", text="شراء 4115")
        rejected = wait_for(lambda s:any(t["state"] == "rejected" and "ستوب قناة" in t.get("last_error","") for t in s["signals"]))
        assert sum(t["state"] == "open" for t in rejected["signals"]) == 1
        request("stop")
        process.wait(timeout=10)
        print("Service smoke passed: version, channel persistence, IPC, execution, partial close, breakeven, restart, direct entry with channel SL and missing-SL rejection.")
    finally:
        if connection:
            connection.close()
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        log.close()


if __name__ == "__main__":
    main()
