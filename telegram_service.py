import asyncio
from dataclasses import dataclass
from typing import List, Optional

from telethon import TelegramClient
from telethon.tl.types import Channel, Chat, User


@dataclass
class TelegramDialogInfo:
    title: str
    telegram_id: int
    entity_type: str
    username: Optional[str] = None
    members_count: Optional[int] = None


class TelegramDialogService:
    def __init__(self, api_id: int, api_hash: str, session_name: str = "tg_session_v1"):
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_name = session_name

    async def _fetch_dialogs_async(self) -> List[TelegramDialogInfo]:
        dialogs_out: List[TelegramDialogInfo] = []

        client = TelegramClient(self.session_name, self.api_id, self.api_hash)
        await client.start()

        async for dialog in client.iter_dialogs():
            entity = dialog.entity

            if isinstance(entity, User):
                continue

            if isinstance(entity, Channel):
                entity_type = "channel" if getattr(entity, "broadcast", False) else "group"
            elif isinstance(entity, Chat):
                entity_type = "group"
            else:
                entity_type = "unknown"

            title = dialog.name or getattr(entity, "title", None) or str(getattr(entity, "id", ""))
            username = getattr(entity, "username", None)
            telegram_id = int(getattr(entity, "id", 0))

            dialogs_out.append(
                TelegramDialogInfo(
                    title=title,
                    telegram_id=telegram_id,
                    entity_type=entity_type,
                    username=username,
                    members_count=None,
                )
            )

        await client.disconnect()
        dialogs_out.sort(key=lambda x: (x.entity_type, x.title.lower()))
        return dialogs_out

    def fetch_dialogs(self) -> List[TelegramDialogInfo]:
        return asyncio.run(self._fetch_dialogs_async())

    def test_connection(self) -> bool:
        async def _test():
            client = TelegramClient(self.session_name, self.api_id, self.api_hash)
            await client.start()
            me = await client.get_me()
            await client.disconnect()
            return me is not None

        return asyncio.run(_test())
