import asyncio
from types import SimpleNamespace

import pytest

from signaldesk.config import Settings
from signaldesk.service import Service


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNALDESK_DATA_DIR", str(tmp_path))
    app = Service("demo")
    app.connect_broker()
    try:
        yield app
    finally:
        app.store.close()
        app.pool.shutdown()


def command(app, action, **values):
    asyncio.run(app.command({"action": action, **values}))


def choose_two(app):
    command(app, "add_channel", id=-1001, name="A")
    command(app, "add_channel", id=-1002, name="B")


def test_legacy_settings_and_duplicate_channel_validation(service):
    cfg = Settings.from_dict({"channel_id": -1001, "channel_name": "A"})
    assert cfg.shared_channel_settings and len(cfg.monitored_channels()) == 1
    command(service, "add_channel", id=-1001, name="A")
    with pytest.raises(ValueError, match="مضافة"):
        command(service, "add_channel", id=-1001, name="A")
    assert service.settings.monitored_channels() == [{"id": -1001, "name": "A"}]


def test_second_channel_selection_persists_without_resetting_first_baseline(service):
    service.gateway.authorized = True
    calls = []

    class Client:
        async def get_messages(self, entity, limit):
            calls.append(entity)
            return [SimpleNamespace(id=100)]

        def is_connected(self):
            return True

    service.gateway.client = Client()
    choose_two(service)
    assert calls == [-1001, -1002]
    service.snapshot()
    assert service.cache["channel_listening"]
    assert [c["id"] for c in service.cache["monitored_channels"]] == [-1001, -1002]
    restored = Service("demo")
    try:
        assert restored.settings.monitored_channels() == [{"id": -1001, "name": "A"}, {"id": -1002, "name": "B"}]
    finally:
        restored.store.close()
        restored.pool.shutdown()


def test_shared_execution_settings_override_archived_secondary_profile(service):
    choose_two(service)
    service.store.set("profile:-1002", Settings(channel_id=-1002, fixed_lot=.09).to_dict())
    command(service, "settings", settings={**service.settings.to_dict(), "fixed_lot": .04, "entry_mode": "direct"})
    for channel in (-1001, -1002):
        cfg = service.engine.settings_for(channel)
        assert cfg.fixed_lot == .04 and cfg.entry_mode == "direct"
        service.engine.receive(channel, 1, "شراء ذهب 4154 ستوب 4153", channel_name=str(channel))
    trades = service.engine.current_signals()
    assert len(trades) == 2 and all(s["state"] == "open" and s["volume"] == .04 for s in trades)
    assert trades[0]["id"] != trades[1]["id"]


def test_independent_profiles_can_be_edited_without_losing_either_channel(service):
    choose_two(service)
    command(service, "settings", settings={**service.settings.to_dict(), "shared_channel_settings": False, "fixed_lot": .04})
    command(service, "edit_channel", id=-1002)
    assert service.settings.channel_id == -1002
    assert [c["id"] for c in service.settings.monitored_channels()] == [-1001, -1002]
    command(service, "settings", settings={**service.settings.to_dict(), "fixed_lot": .08, "entry_mode": "direct"})
    assert service.engine.settings_for(-1001).fixed_lot == .04
    assert service.engine.settings_for(-1002).fixed_lot == .08
    assert service.engine.settings_for(-1001).entry_mode == "range"
    command(service, "edit_channel", id=-1001)
    assert service.settings.channel_id == -1001
    assert service.settings.fixed_lot == .04
    restored = Service("demo")
    try:
        restored.connect_broker()
        assert not restored.settings.shared_channel_settings
        assert [c["id"] for c in restored.settings.monitored_channels()] == [-1001, -1002]
        assert restored.engine.settings_for(-1002).fixed_lot == .08
    finally:
        restored.store.close()
        restored.pool.shutdown()


def test_targets_repeat_and_same_message_ids_remain_linked_to_their_channel(service):
    choose_two(service)
    command(service, "settings", settings={**service.settings.to_dict(), "targets_mode": "channel"})
    engine = service.engine
    first = engine.receive(-1001, 1, "شراء ذهب 4154 ستوب 4153", channel_name="A")
    second = engine.receive(-1002, 1, "شراء ذهب 4154 ستوب 4153", channel_name="B")
    engine.receive(-1002, 2, "هدف 4160", reply=1)
    assert not engine.signals[first]["channel_targets"]
    assert engine.signals[second]["channel_targets"] == [4160]
    # Close A only. B's availability must not re-enter A or duplicate B.
    ticket = engine.signals[first]["position_id"]
    service.broker.close(ticket, .03, "test")
    engine.reconcile()
    engine.receive(-1002, 3, "متاحة", reply=1)
    assert len(service.broker.positions()) == 1
    reopened = engine.receive(-1001, 3, "متاحة", reply=1)
    assert engine.signals[reopened]["root"] == first
    assert len(service.broker.positions()) == 2
    assert service.store.message(-1001, 3)["signal"] != service.store.message(-1002, 3)["signal"]


def test_removal_cancels_only_removed_pending_signals_and_keeps_open_management(service):
    choose_two(service)
    engine = service.engine
    first = engine.receive(-1001, 1, "شراء ذهب 4160 ستوب 4159", channel_name="A")
    second = engine.receive(-1002, 1, "شراء ذهب 4160 ستوب 4159", channel_name="B")
    opened = engine.receive(-1002, 2, "شراء ذهب 4154 ستوب 4153", channel_name="B")
    assert engine.signals[opened]["state"] == "open"
    service.deferred = [[-1001, 5, "شراء 4160", None, False, 1, "A"], [-1002, 5, "شراء 4160", None, False, 1, "B"]]
    command(service, "remove_channel", id=-1002, cancel_previous=True)
    assert engine.signals[first]["state"] == "pending"
    assert engine.signals[second]["state"] == "cancelled"
    assert engine.signals[opened]["state"] == "open"
    assert [m[0] for m in service.deferred] == [-1001]
    service.broker.set_price(engine.signals[opened]["fill"] + 5)
    engine.step()
    assert engine.signals[opened]["secured"] and engine.signals[opened]["volume"] == .02


def test_swapping_primary_with_secondary_keeps_both_pending_signals(service):
    choose_two(service)
    for channel in (-1001, -1002):
        service.engine.receive(channel, 1, "شراء ذهب 4160 ستوب 4159")
    command(service, "choose_channel", id=-1002, name="B", cancel_previous=True)
    assert service.settings.channel_id == -1002
    assert [c["id"] for c in service.settings.monitored_channels()] == [-1001, -1002]
    assert all(s["state"] == "pending" for s in service.engine.current_signals())


def test_account_switch_does_not_inherit_second_channel(service):
    choose_two(service)
    identity = service.store.account
    service.activate_account("other:123")
    assert not service.settings.monitored_channels() and not service.gateway.channel_selection()
    service.activate_account(identity)
    assert [c["id"] for c in service.settings.monitored_channels()] == [-1001, -1002]
    assert [c["id"] for c in service.gateway.channel_selection()] == [-1001, -1002]


def test_old_two_channel_settings_migrate_without_losing_selection_or_profiles():
    cfg = Settings.from_dict({"channel_id":-1001, "channel_name":"A", "second_channel_id":-1002,
                              "second_channel_name":"B", "shared_channel_settings":False, "fixed_lot":.08})
    assert cfg.monitored_channels() == [{"id":-1001,"name":"A"},{"id":-1002,"name":"B"}]
    assert not cfg.shared_channel_settings and cfg.fixed_lot == .08
    saved = cfg.to_dict()
    assert "second_channel_id" not in saved
    assert Settings.from_dict(saved).monitored_channels() == cfg.monitored_channels()


def test_many_channels_persist_and_execute_even_with_earlier_open_trades(service):
    for i in range(12):
        command(service, "add_channel", id=-1001-i, name=f"Channel {i}")
    for c in service.settings.monitored_channels():
        service.engine.receive(c["id"], 1, "شراء ذهب 4154 ستوب 4153", channel_name=c["name"])
    assert len(service.broker.positions()) == 12
    restored = Service("demo")
    try:
        restored.connect_broker()
        assert len(restored.settings.monitored_channels()) == 12
        assert len(restored.engine.current_signals()) == 12
    finally:
        restored.store.close()
        restored.pool.shutdown()


def test_removing_channel_being_edited_loads_next_profile_and_preserves_removed_profile(service):
    choose_two(service)
    command(service, "settings", settings={**service.settings.to_dict(),"shared_channel_settings":False,"fixed_lot":.04})
    command(service, "edit_channel", id=-1002)
    command(service, "settings", settings={**service.settings.to_dict(),"fixed_lot":.08})
    command(service, "remove_channel", id=-1002)
    assert service.settings.channel_id == -1001 and service.settings.fixed_lot == .04
    assert service.store.get("profile:-1002")["fixed_lot"] == .08
    command(service, "add_channel", id=-1002, name="B")
    assert service.engine.settings_for(-1002).fixed_lot == .08


def test_removing_all_channels_does_not_restore_legacy_selection_on_restart(service):
    choose_two(service)
    command(service, "remove_channel", id=-1001)
    command(service, "remove_channel", id=-1002)
    assert service.settings.monitored_channels() == [] and service.settings.channel_id == 0
    restored = Service("demo")
    try:
        assert restored.settings.monitored_channels() == [] and restored.settings.channel_id == 0
    finally:
        restored.store.close()
        restored.pool.shutdown()


def test_removal_during_mt5_disconnection_cancels_persisted_pending_signal(service):
    choose_two(service)
    identifier = service.engine.receive(-1002, 1, "شراء ذهب 4160 ستوب 4159")
    assert service.engine.signals[identifier]["state"] == "pending"
    service.engine = None
    command(service, "remove_channel", id=-1002, cancel_previous=True)
    restored = Service("demo")
    try:
        restored.connect_broker()
        assert restored.engine.signals[identifier]["state"] == "cancelled"
    finally:
        restored.store.close()
        restored.pool.shutdown()
