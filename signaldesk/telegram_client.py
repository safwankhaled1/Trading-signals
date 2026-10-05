from __future__ import annotations

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
        self.message_floor = 0
        self.baseline_ready = False
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
        self.baseline_ready = False
        self.selection_generation += 1
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
        await self.select_channel(self.channel_id, self.channel_name)
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

    def set_channel(self, identifier, name):
        self.channel_id, self.channel_name = int(identifier), name
        self.baseline_ready = False
        self.message_floor = 0
        self.selection_generation += 1

    async def select_channel(self, identifier, name):
        self.set_channel(identifier, name)
        generation = self.selection_generation
        if not self.channel_id or not self.authorized or not self.client:
            return
        try:
            entity = self.channel_entities.get(self.channel_id, self.channel_id)
            latest = await self.client.get_messages(entity, limit=1)
            if generation != self.selection_generation:
                return
            self.message_floor = latest[0].id if latest else 0
            self.baseline_ready = True
            self.error = ""
        except Exception as exc:
            if generation != self.selection_generation:
                return
            self.error = "تعذر بدء مراقبة القناة: " + str(exc)
            self.notify(self.error)
            raise

    async def _new_message(self, event):
        if event.chat_id != self.channel_id or not self.channel_id:
            return
        if not self.baseline_ready or event.id <= self.message_floor:
            return
        self.message_floor = event.id
        self.callback(event.chat_id, event.id, event.raw_text, event.reply_to_msg_id, False,
                      time.time(), self.channel_name)

    async def _edited_message(self, event):
        if event.chat_id != self.channel_id or not self.channel_id or not self.baseline_ready:
            return
        # The engine applies edits only to known messages, never imports an old entry.
        self.callback(event.chat_id, event.id, event.raw_text, event.reply_to_msg_id, True,
                      time.time(), self.channel_name)

    async def disconnect(self):
        if self.client:
            await self.client.disconnect()
        self.state = "غير متصل"
        self.authorized = False
        self.baseline_ready = False
        self.selection_generation += 1
        self.notify(self.state)

    async def logout(self):
        if self.client:
            await self.client.log_out()
        if self.path.exists():
            self.path.unlink()
        self.state = "غير متصل"
        self.authorized = False
        self.baseline_ready = False
        self.selection_generation += 1
        self.channels = []
        self.notify(self.state)
