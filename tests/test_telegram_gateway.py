import asyncio
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from signaldesk.service import Service
from signaldesk.telegram_client import TelegramGateway


class ChannelClient:
    def __init__(self, latest=100):
        self.latest = latest
        self.entities = []

    async def get_messages(self, entity, limit):
        assert limit == 1
        self.entities.append(entity)
        return [SimpleNamespace(id=self.latest)] if self.latest else []


def event(mid, channel=-1001, outgoing=True):
    return SimpleNamespace(chat_id=channel, id=mid, raw_text="اشتري btcusd \n84905",
                           reply_to_msg_id=None, outgoing=outgoing,
                           message=SimpleNamespace(date=datetime(2000, 1, 1, tzinfo=timezone.utc)))


@pytest.mark.parametrize("outgoing", [True, False])
def test_new_messages_use_id_baseline_and_local_receipt_time(tmp_path, outgoing):
    received = []
    gateway = TelegramGateway(tmp_path, lambda *args: received.append(args), lambda _: None)
    gateway.client = ChannelClient()
    gateway.authorized = True
    entity = object()
    gateway.channel_entities[-1001] = entity

    async def run():
        await gateway.select_channel(-1001, "تجربة")
        assert gateway.client.entities == [entity]
        await gateway._new_message(event(99, outgoing=outgoing))
        await gateway._new_message(event(100, outgoing=outgoing))
        await gateway._new_message(event(101, channel=-1002, outgoing=outgoing))
        before = time.time()
        await gateway._new_message(event(101, outgoing=outgoing))
        after = time.time()
        await gateway._new_message(event(101, outgoing=outgoing))
        assert len(received) == 1
        assert received[0][:5] == (-1001, 101, "اشتري btcusd \n84905", None, False)
        assert before <= received[0][5] <= after
        assert received[0][6] == "تجربة"
        gateway.client.latest = 200
        await gateway.select_channel(-1002, "ثانية")
        await gateway._new_message(event(102))
        await gateway._new_message(event(200, channel=-1002))
        await gateway._new_message(event(201, channel=-1002, outgoing=outgoing))
        assert [args[1] for args in received] == [101, 201]

    asyncio.run(run())


def test_event_handlers_accept_own_posts(tmp_path, monkeypatch):
    import telethon
    from signaldesk import telegram_client
    handlers = []

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def add_event_handler(self, callback, builder):
            handlers.append(builder)

        async def connect(self):
            pass

        async def is_user_authorized(self):
            return False

    monkeypatch.setattr(telethon, "TelegramClient", Client)
    monkeypatch.setattr(telegram_client, "load_secret", lambda _: {})
    gateway = TelegramGateway(tmp_path, lambda *args: None, lambda _: None)
    asyncio.run(gateway.connect(1, "test"))
    assert len(handlers) == 2
    assert all(builder.incoming is None and builder.outgoing is None for builder in handlers)


def test_failed_channel_baseline_cannot_receive_until_retry(tmp_path):
    received = []
    gateway = TelegramGateway(tmp_path, lambda *args: received.append(args), lambda _: None)
    gateway.authorized = True

    async def fail(*args, **kwargs):
        raise ValueError("channel inaccessible")

    gateway.client = SimpleNamespace(get_messages=fail)

    async def run():
        with pytest.raises(ValueError):
            await gateway.select_channel(-1001, "تجربة")
        await gateway._new_message(event(101))
        assert not gateway.baseline_ready and not received
        assert "channel inaccessible" in gateway.error
        gateway.client = ChannelClient()
        await gateway.select_channel(-1001, "تجربة")
        assert gateway.baseline_ready and not gateway.error
        await gateway._new_message(event(101))
        assert len(received) == 1

    asyncio.run(run())


def test_own_post_reaches_engine_despite_old_telegram_timestamp(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNALDESK_DATA_DIR", str(tmp_path))
    service = Service("demo")
    try:
        service.connect_broker()
        service.broker.symbol = "BTCUSD"
        service.broker.bid, service.broker.spread = 84905, 0
        service.gateway.client = ChannelClient()
        service.gateway.authorized = True

        async def run():
            await service.gateway.select_channel(-1001, "تجربة")
            await service.gateway._new_message(event(100))
            await service.gateway._edited_message(event(99))
            assert not service.engine.current_signals()
            await service.gateway._new_message(event(101))
            assert len(service.broker.positions()) == 1
            assert service.engine.current_signals()[0]["state"] == "open"
            assert not service.deferred
            await service.gateway._new_message(event(101))
            assert len(service.broker.positions()) == 1
            assert any(e["message"] == "وصلت رسالة جديدة من القناة" for e in service.store.events())

        asyncio.run(run())
    finally:
        service.store.close()
        service.pool.shutdown()


def test_late_channel_response_cannot_reenable_previous_channel(tmp_path):
    gateway = TelegramGateway(tmp_path, lambda *args: None, lambda _: None)
    gateway.authorized = True

    async def run():
        first = asyncio.Future()

        class Client:
            async def get_messages(self, entity, limit):
                return await first if entity == -1001 else [SimpleNamespace(id=200)]

        gateway.client = Client()
        task = asyncio.create_task(gateway.select_channel(-1001, "A"))
        await asyncio.sleep(0)
        await gateway.select_channel(-1002, "B")
        first.set_result([SimpleNamespace(id=100)])
        await task
        assert gateway.channel_id == -1002 and gateway.message_floor == 200
        assert gateway.baseline_ready

    asyncio.run(run())


def test_two_channels_keep_independent_floors_names_and_edits(tmp_path):
    received = []
    gateway = TelegramGateway(tmp_path, lambda *args: received.append(args), lambda _: None)
    gateway.authorized = True

    class Client:
        async def get_messages(self, entity, limit):
            return [SimpleNamespace(id=900 if entity == -1001 else 100)]

    gateway.client = Client()

    async def run():
        await gateway.select_channels([{"id": -1001, "name": "A"}, {"id": -1002, "name": "B"}])
        for channel, mid in [(-1001, 900), (-1002, 100), (-1002, 101), (-1001, 901), (-1002, 101), (-1003, 902)]:
            await gateway._new_message(event(mid, channel=channel))
        await gateway._edited_message(event(101, channel=-1002))
        assert [(a[0], a[1], a[4], a[6]) for a in received] == [
            (-1002, 101, False, "B"), (-1001, 901, False, "A"), (-1002, 101, True, "B")]
        assert gateway.baseline_ready

    asyncio.run(run())


def test_adding_or_removing_second_channel_keeps_first_receiving(tmp_path):
    received = []
    gateway = TelegramGateway(tmp_path, lambda *args: received.append(args), lambda _: None)
    gateway.client = ChannelClient()
    gateway.authorized = True

    async def run():
        await gateway.select_channels([{"id": -1001, "name": "A"}])
        await gateway._new_message(event(101))
        gateway.client.latest = 200
        await gateway.select_channels([{"id": -1001, "name": "A"}, {"id": -1002, "name": "B"}])
        assert gateway.client.entities == [-1001, -1002]
        await gateway._new_message(event(102))
        await gateway._new_message(event(200, channel=-1002))
        await gateway._new_message(event(201, channel=-1002))
        await gateway.select_channels([{"id": -1001, "name": "A"}])
        await gateway._new_message(event(202, channel=-1002))
        await gateway._new_message(event(103))
        assert [(a[0], a[1]) for a in received] == [(-1001, 101), (-1001, 102), (-1002, 201), (-1001, 103)]

    asyncio.run(run())


def test_second_channel_failure_does_not_stop_first_and_retry_does_not_skip_first(tmp_path):
    received = []
    gateway = TelegramGateway(tmp_path, lambda *args: received.append(args), lambda _: None)
    gateway.authorized = True
    failed = True

    class Client:
        async def get_messages(self, entity, limit):
            if entity == -1002 and failed:
                raise ValueError("inaccessible")
            return [SimpleNamespace(id=100)]

    gateway.client = Client()

    async def run():
        nonlocal failed
        channels = [{"id": -1001, "name": "A"}, {"id": -1002, "name": "B"}]
        with pytest.raises(ValueError, match="B.*inaccessible"):
            await gateway.select_channels(channels)
        await gateway._new_message(event(101))
        await gateway._new_message(event(101, channel=-1002))
        assert [(a[0], a[1]) for a in received] == [(-1001, 101)]
        failed = False
        await gateway.select_channels(channels)
        await gateway._new_message(event(101))
        await gateway._new_message(event(101, channel=-1002))
        assert [(a[0], a[1]) for a in received] == [(-1001, 101), (-1002, 101)]
        assert gateway.baseline_ready and not gateway.error

    asyncio.run(run())


def test_removed_channel_cannot_return_from_inflight_baseline(tmp_path):
    gateway = TelegramGateway(tmp_path, lambda *args: None, lambda _: None)
    gateway.authorized = True

    async def run():
        delayed = asyncio.Future()

        class Client:
            async def get_messages(self, entity, limit):
                return await delayed if entity == -1002 else [SimpleNamespace(id=100)]

        gateway.client = Client()
        first = [{"id": -1001, "name": "A"}]
        await gateway.select_channels(first)
        task = asyncio.create_task(gateway.select_channels(first + [{"id": -1002, "name": "B"}]))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await gateway.select_channels(first)
        delayed.set_result([SimpleNamespace(id=200)])
        await task
        assert gateway.channel_selection() == first and gateway.baseline_ready

    asyncio.run(run())


def test_overlapping_syncs_share_baseline_request_without_resetting_new_messages(tmp_path):
    received = []
    gateway = TelegramGateway(tmp_path, lambda *args: received.append(args), lambda _: None)
    gateway.authorized = True

    async def run():
        pending = asyncio.Future()
        calls = []

        class Client:
            async def get_messages(self, entity, limit):
                calls.append(entity)
                return await pending

        gateway.client = Client()
        channels = [{"id": -1001, "name": "A"}]
        first = asyncio.create_task(gateway.select_channels(channels))
        await asyncio.sleep(0)
        second = asyncio.create_task(gateway.select_channels(channels))
        await asyncio.sleep(0)
        pending.set_result([SimpleNamespace(id=100)])
        await asyncio.gather(first, second)
        await gateway._new_message(event(101))
        assert calls == [-1001] and len(received) == 1
        assert gateway.message_floor == 101

    asyncio.run(run())
