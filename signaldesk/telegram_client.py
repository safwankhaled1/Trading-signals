from __future__ import annotations

import asyncio
import time

from .security import load_secret, save_secret


class TelegramGateway:
    def __init__(self, directory, callback, status):
        self.path = directory / "telegram.secret"
        self.callback, self.notify = callback, status
        self.client = None
        self.code_hash = None
        self.phone = ""
        self.channel_id = 0
        self.channel_name = ""
        self.state = "غير متصل"
        self.channels = []
        self.error = ""
        self.busy = False
        self.authorized = False
        self.monitored = {}
        self.channel_entities = {}
        self.selection_generation = 0

    async def connect(self, api_id=None, api_hash=None):
        from telethon import TelegramClient, events
        from telethon.sessions import StringSession
        saved = load_secret(self.path)
        api_id = int(api_id or saved.get("api_id", 0))
        api_hash = api_hash or saved.get("api_hash", "")
        if api_id <= 0 or not api_hash:
            raise ValueError("أدخل API ID وAPI Hash من my.telegram.org")
        if self.client:
            await self.client.disconnect()
        self.authorized = False
        self.invalidate_channels()
        self.state = "جارٍ الاتصال"
        self.notify(self.state)
        self.client = TelegramClient(StringSession(saved.get("session", "")), api_id, api_hash,
                                     receive_updates=True, catch_up=False, auto_reconnect=True)
        self.credentials = {"api_id": api_id, "api_hash": api_hash}
        self.client.add_event_handler(self._new_message, events.NewMessage())
        self.client.add_event_handler(self._edited_message, events.MessageEdited())
        await self.client.connect()
        if await self.client.is_user_authorized():
            await self.ready()
        else:
            self.state = "بانتظار رقم الهاتف"
            self.notify(self.state)

    async def ready(self):
        save_secret(self.path, {**self.credentials, "session": self.client.session.save()})
        self.state = "متصل"
        self.authorized = True
        self.error = ""
        self.channels = []
        self.channel_entities = {}
        async for dialog in self.client.iter_dialogs():
            if dialog.is_channel and getattr(dialog.entity, "broadcast", False):
                self.channels.append({"id": dialog.id, "name": dialog.name})
                self.channel_entities[dialog.id] = dialog.entity
        await self.select_channels(self.channel_selection(), reset=True)
        self.notify(self.state)

    async def send_code(self, phone):
        if not self.client:
            raise ValueError("اربط بيانات API أولًا")
        self.phone = phone.strip()
        response = await self.client.send_code_request(self.phone)
        self.code_hash = response.phone_code_hash
        self.state = "بانتظار رمز التحقق"
        self.notify(self.state)

    async def sign_in(self, code="", password=""):
        from telethon.errors import SessionPasswordNeededError
        if not self.client:
            raise ValueError("اربط بيانات API وأرسل رمز التحقق أولًا")
        try:
            if password:
                await self.client.sign_in(password=password)
            else:
                await self.client.sign_in(phone=self.phone, code=code.strip(), phone_code_hash=self.code_hash)
        except SessionPasswordNeededError:
            self.state = "بانتظار كلمة مرور التحقق بخطوتين"
            self.notify(self.state)
            return
        await self.ready()

    @property
    def message_floor(self):
        return self.monitored.get(self.channel_id, {}).get("floor", 0)

    @property
    def baseline_ready(self):
        return bool(self.monitored) and all(c["ready"] for c in self.monitored.values())

    def channel_selection(self):
        return [{"id": identifier, "name": state["name"]} for identifier, state in self.monitored.items()]

    def channel_statuses(self):
        return [{"id": identifier, "name": state["name"], "ready": state["ready"], "error": state["error"]}
                for identifier, state in self.monitored.items()]

    def invalidate_channels(self):
        self.set_channels(self.channel_selection(), reset=True)

    def set_channels(self, channels, reset=False):
        chosen = [{"id": int(c["id"]), "name": str(c["name"])} for c in channels if c["id"]]
        if len({c["id"] for c in chosen}) != len(chosen):
            raise ValueError("القناة مضافة إلى المراقبة بالفعل")
        previous = self.monitored
        self.monitored = {}
        for channel in chosen:
            identifier = channel["id"]
            state = previous.get(identifier) if not reset else None
            state = state if state is not None else {"floor": 0, "ready": False, "error": ""}
            state["name"] = channel["name"]
            self.monitored[identifier] = state
        self.channel_id = chosen[0]["id"] if chosen else 0
        self.channel_name = chosen[0]["name"] if chosen else ""
        self.selection_generation += 1

    def set_channel(self, identifier, name):
        self.set_channels([{"id": identifier, "name": name}], reset=True)

    async def select_channel(self, identifier, name):
        await self.select_channels([{"id": identifier, "name": name}], reset=True)

    async def select_channels(self, channels, reset=False):
        self.set_channels(channels, reset=reset)
        if not self.authorized or not self.client:
            return
        client = self.client
        slots = asyncio.Semaphore(4)

        async def baseline(identifier, state):
            try:
                entity = self.channel_entities.get(identifier, identifier)
                async with slots:
                    latest = await client.get_messages(entity, limit=1)
                if self.monitored.get(identifier) is not state or self.client is not client:
                    return
                state.update(floor=latest[0].id if latest else 0, ready=True, error="")
            except Exception as exc:
                if self.monitored.get(identifier) is state and self.client is client:
                    state["error"] = "تعذر بدء مراقبة القناة " + state["name"] + ": " + str(exc)

        tasks = []
        for identifier, state in self.monitored.items():
            if not state["ready"]:
                task = state.get("task")
                if task is None or task.done():
                    task = state["task"] = asyncio.create_task(baseline(identifier, state))
                tasks.append(asyncio.shield(task))
        await asyncio.gather(*tasks)
        errors = [state["error"] for state in self.monitored.values() if state["error"]]
        self.error = " • ".join(errors)
        if errors:
            self.notify(self.error)
            raise ValueError(self.error)

    async def _new_message(self, event):
        state = self.monitored.get(event.chat_id)
        if not state or not state["ready"] or event.id <= state["floor"]:
            return
        state["floor"] = event.id
        self.callback(event.chat_id, event.id, event.raw_text, event.reply_to_msg_id, False,
                      time.time(), state["name"])

    async def _edited_message(self, event):
        state = self.monitored.get(event.chat_id)
        if not state or not state["ready"]:
            return
        # The engine applies edits only to known messages, never imports an old entry.
        self.callback(event.chat_id, event.id, event.raw_text, event.reply_to_msg_id, True,
                      time.time(), state["name"])

    async def disconnect(self):
        if self.client:
            await self.client.disconnect()
        self.state = "غير متصل"
        self.authorized = False
        self.invalidate_channels()
        self.notify(self.state)

    async def logout(self):
        if self.client:
            await self.client.log_out()
        if self.path.exists():
            self.path.unlink()
        self.state = "غير متصل"
        self.authorized = False
        self.invalidate_channels()
        self.channels = []
        self.notify(self.state)
