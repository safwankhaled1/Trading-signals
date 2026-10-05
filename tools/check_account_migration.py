"""Review a read-only copy of a legacy database; never connect to MT5 or Telegram."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

project = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project))
from signaldesk.config import Settings
from signaldesk.reports import build_report
from signaldesk.store import Store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    args = parser.parse_args()
    source = Path(args.database).resolve()
    target = project / ".local-data" / f"migration-review-{time.time_ns()}.sqlite"
    target.parent.mkdir(exist_ok=True)
    original = sqlite3.connect("file:" + source.as_posix() + "?mode=ro", uri=True)
    copy = sqlite3.connect(target)
    try:
        original.backup(copy)
    finally:
        original.close()
        copy.close()
    store = Store(target)
    try:
        all_signals = store.signals()
        last = store.get("last_account")
        identities = {s["account"] for s in all_signals}
        if last:
            identities.add(last)
        for identity in identities:
            store.activate_account(identity)
            expected = {s["id"] for s in all_signals if s["account"] == identity}
            assert {s["id"] for s in store.signals()} == expected
            assert all(e["account"] == identity for e in store.events())
            report = build_report(target, "2020-01-01", "2030-01-01", account=identity)
            assert all(row["signal"] in expected for row in report["rows"])
            Settings.from_dict(store.get("settings", {}))
        store.account = None
        assert len(store.signals()) == len(all_signals)
        print(json.dumps({"accounts_reviewed": len(identities), "all_signals_preserved": True,
                          "account_reports_and_events_separate": True,
                          "source_database_unchanged": True}, ensure_ascii=False))
    finally:
        store.close()


if __name__ == "__main__":
    main()
