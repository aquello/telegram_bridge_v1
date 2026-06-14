import logging
from copy import deepcopy
from datetime import datetime, timezone
from typing import Iterable, Optional

from telethon import TelegramClient, events

from .db import (
    get_channel_config_by_telegram_id,
    init_schema,
    insert_raw_message,
    insert_signal,
    insert_sl_move,
    raw_message_exists,
)
from .universal_parser import parse_universal

logger = logging.getLogger(__name__)


class TelegramSignalListener:
    def __init__(self, api_id: int, api_hash: str, session_name: str = "tg_session_v1"):
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_name = session_name
        self.client = TelegramClient(session_name, api_id, api_hash)

    async def start(self):
        await self.client.start()
        logger.info("Cliente Telethon v1 iniciado")
        self._register_handlers()

    def _normalize_lookup_ids(self, raw_id: int) -> Iterable[int]:
        yield raw_id
        yield -(1_000_000_000_000 + abs(raw_id))

    def _resolve_channel_cfg(self, raw_id: int):
        for candidate in self._normalize_lookup_ids(raw_id):
            cfg = get_channel_config_by_telegram_id(candidate)
            if cfg is not None:
                return cfg, candidate
        return None, None

    def _process_message(self, raw_id: int, message_id: int, msg_date_ts: int, text: str, channel_cfg: dict, reply_to_id: Optional[int]):
        if not text or not text.strip():
            return

        channel_name = channel_cfg["channel_name"]
        channel_id_str = str(raw_id)

        if raw_message_exists(channel_id_str, message_id):
            logger.info("[SKIP_DUP] canal=%s msg=%s", channel_name, message_id)
            return

        raw_msg_id = insert_raw_message(
            {
                "channel_id": channel_id_str,
                "channel_name": channel_name,
                "message_id": message_id,
                "date": msg_date_ts,
                "text": text,
                "raw_json": None,
            }
        )

        logger.info("[PARSE] canal=%s id=%s text=%r", channel_name, message_id, text[:120])

        signals_raw = parse_universal(
            text,
            channel_cfg,
            tg_message_id=message_id,
            tg_reply_to_id=reply_to_id,
        )

        if not signals_raw:
            logger.info("[NONE] canal=%s id=%s", channel_name, message_id)
            return

        enriched = []

        for sd in signals_raw:
            sd = dict(sd)
            sd["raw_message_id"] = raw_msg_id
            sd.setdefault("tg_message_id", message_id)
            sd.setdefault("tg_reply_to_id", reply_to_id)
            sd["channel_name"] = channel_name

            action = sd.get("action", "")

            if action == "SL_MOVE":
                sid = insert_sl_move(
                    sd["symbol"],
                    channel_name,
                    sd["sl"],
                    reply_to_id or 0,
                )
                logger.info("SL_MOVE id=%s canal=%s sl=%s", sid, channel_name, sd["sl"])
                continue

            if action in ("TP_DONE",):
                continue

            if action == "OPEN" and sd.get("tp1"):
                tp_final = next(
                    (sd.get(f"tp{i}") for i in range(10, 0, -1) if sd.get(f"tp{i}")),
                    None,
                )
                if tp_final:
                    sd["tp"] = tp_final

            enriched.append(sd)

        if channel_cfg.get("enable_reverse", 0):
            for orig in list(enriched):
                if orig.get("action") != "OPEN":
                    continue
                if not orig.get("symbol") or not orig.get("direction"):
                    continue

                rev = deepcopy(orig)

                if channel_cfg.get("magic_reverse"):
                    rev["magic"] = channel_cfg["magic_reverse"]

                rev["direction"] = "SELL" if orig["direction"] == "BUY" else "BUY"

                sl, tp = orig.get("sl"), orig.get("tp")
                if sl is not None and tp is not None:
                    rev["sl"], rev["tp"] = tp, sl

                for k in ("tp1", "tp2", "tp3", "tp4", "tp5", "tp_stage"):
                    rev[k] = 0

                rev["tg_message_id"] = -1 * orig["tg_message_id"]

                if channel_cfg.get("execution_type_rev"):
                    rev["execution_type"] = channel_cfg["execution_type_rev"]

                enriched.append(rev)

        for sig in enriched:
            if not sig.get("action"):
                continue
            sid = insert_signal(sig)
            logger.info(
                "Signal id=%s action=%s symbol=%s dir=%s magic=%s",
                sid,
                sig.get("action"),
                sig.get("symbol"),
                sig.get("direction"),
                sig.get("magic"),
            )

    def _register_handlers(self):
        @self.client.on(events.NewMessage())
        async def handler(event):
            try:
                chat = await event.get_chat()
                raw_id = chat.id
                cfg, _ = self._resolve_channel_cfg(raw_id)
                if cfg is None:
                    return

                msg = event.message
                text = msg.message or ""
                self._process_message(
                    raw_id=raw_id,
                    message_id=msg.id,
                    msg_date_ts=int(msg.date.timestamp()),
                    text=text,
                    channel_cfg=cfg,
                    reply_to_id=msg.reply_to_msg_id,
                )
            except Exception as e:
                logger.exception("Error procesando mensaje: %s", e)

    async def replay_messages(
        self,
        selected_channel_ids,
        date_from: datetime,
        date_to: Optional[datetime] = None,
        progress_callback=None,
    ):
        await self.client.start()
        logger.info("Cliente Telethon iniciado para replay")

        total_processed = 0
        total_channels = len(selected_channel_ids)

        if date_from.tzinfo is None:
            date_from = date_from.replace(tzinfo=timezone.utc)
        else:
            date_from = date_from.astimezone(timezone.utc)

        if date_to:
            if date_to.tzinfo is None:
                date_to = date_to.replace(tzinfo=timezone.utc)
            else:
                date_to = date_to.astimezone(timezone.utc)

        for idx, channel_id in enumerate(selected_channel_ids, start=1):
            cfg = get_channel_config_by_telegram_id(int(channel_id))
            if cfg is None:
                alt = -(1_000_000_000_000 + abs(int(channel_id)))
                cfg = get_channel_config_by_telegram_id(alt)
            if cfg is None:
                logger.warning("Canal no configurado para replay: %s", channel_id)
                continue

            if progress_callback:
                progress_callback(f"[{idx}/{total_channels}] Replay {cfg['channel_name']}...")

            entity = await self.client.get_entity(int(channel_id))
            channel_processed = 0

            async for msg in self.client.iter_messages(
                entity,
                offset_date=date_from,
                reverse=True,
            ):
                if msg is None or msg.message is None:
                    continue
                if not getattr(msg, "date", None):
                    continue

                msg_dt = msg.date
                if msg_dt.tzinfo is None:
                    msg_dt = msg_dt.replace(tzinfo=timezone.utc)
                else:
                    msg_dt = msg_dt.astimezone(timezone.utc)

                if msg_dt < date_from:
                    continue
                if date_to and msg_dt > date_to:
                    break

                self._process_message(
                    raw_id=int(channel_id),
                    message_id=msg.id,
                    msg_date_ts=int(msg.date.timestamp()),
                    text=msg.message or "",
                    channel_cfg=cfg,
                    reply_to_id=msg.reply_to_msg_id,
                )
                channel_processed += 1
                total_processed += 1

            if progress_callback:
                progress_callback(f"[{idx}/{total_channels}] {cfg['channel_name']}: {channel_processed} mensajes")

        return total_processed

    def run_forever(self):
        init_schema()
        with self.client:
            self.client.loop.run_until_complete(self.start())
            logger.info("Escuchando (v1)...")
            self.client.run_until_disconnected()

    def run_replay(self, selected_channel_ids, date_from: datetime, date_to: Optional[datetime] = None, progress_callback=None):
        init_schema()
        with self.client:
            total = self.client.loop.run_until_complete(
                self.replay_messages(
                    selected_channel_ids=selected_channel_ids,
                    date_from=date_from,
                    date_to=date_to,
                    progress_callback=progress_callback,
                )
            )
        return total
