"""
listener.py – Escucha Telegram y procesa señales (telegram_bridge_v1)

Mejoras:
- Lookup solo por telegram_id.
- Restaura estado multi-open al arrancar.
- Unifica todas las inserciones en insert_signal().
- Señal inversa saneada (tp1..tp10).
"""

import logging
from copy import deepcopy
from typing import Dict, Optional

from telethon import TelegramClient, events

from .db import (
    get_channel_config_by_telegram_id,
    get_last_open_signals,
    init_schema,
    insert_raw_message,
    insert_signal,
)
from .universal_parser import parse_universal, restore_state_from_db

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
        open_sigs = get_last_open_signals()
        restore_state_from_db(open_sigs)
        logger.info("[BOOT] Estado restaurado: %d OPEN activas", len(open_sigs))
        self._register_handlers()

    def _lookup_cfg(self, raw_id: int) -> Optional[Dict]:
        cfg = get_channel_config_by_telegram_id(raw_id)
        if cfg is None:
            cfg = get_channel_config_by_telegram_id(-(1_000_000_000_000 + abs(raw_id)))
        return cfg

    def _register_handlers(self):
        @self.client.on(events.NewMessage())
        async def handler(event):
            try:
                chat = await event.get_chat()
                raw_id = chat.id
                cfg = self._lookup_cfg(raw_id)
                if cfg is None:
                    return

                msg = event.message
                text = msg.message or ""
                if not text.strip():
                    return

                channel_name = cfg["channel_name"]
                reply_to_id: Optional[int] = msg.reply_to_msg_id

                raw_msg_id = insert_raw_message(
                    {
                        "channel_id": str(raw_id),
                        "channel_name": channel_name,
                        "message_id": msg.id,
                        "date": int(msg.date.timestamp()),
                        "text": text,
                        "raw_json": None,
                    }
                )

                logger.info("[PARSE] canal=%s id=%s text=%r", channel_name, msg.id, text[:160])
                signals_raw = parse_universal(
                    text,
                    cfg,
                    tg_message_id=msg.id,
                    tg_reply_to_id=reply_to_id,
                )
                if not signals_raw:
                    logger.info("[NONE] canal=%s id=%s", channel_name, msg.id)
                    return

                enriched = []
                for sd in signals_raw:
                    sig = dict(sd)
                    sig["raw_message_id"] = raw_msg_id
                    sig["telegram_id"] = cfg["telegram_id"]
                    sig.setdefault("tg_message_id", msg.id)
                    sig.setdefault("tg_reply_to_id", reply_to_id)
                    sig["channel_name"] = channel_name
                    if sig.get("action") == "OPEN":
                        final_tp = next(
                            (sig.get(f"tp{i}") for i in range(10, 0, -1) if sig.get(f"tp{i}") is not None),
                            None
                        )
                        if final_tp is not None:
                            sig["tp"] = final_tp
                    enriched.append(sig)

                if cfg.get("enable_reverse", 0):
                    reverse_batch = []
                    for orig in enriched:
                        if orig.get("action") != "OPEN":
                            continue
                        if not orig.get("symbol") or not orig.get("direction"):
                            continue
                        rev = deepcopy(orig)
                        if cfg.get("magic_reverse") is not None:
                            rev["magic"] = cfg["magic_reverse"]
                        rev["direction"] = "SELL" if orig["direction"] == "BUY" else "BUY"
                        sl, tp = orig.get("sl"), orig.get("tp")
                        if sl is not None and tp is not None:
                            rev["sl"], rev["tp"] = tp, sl
                        for i in range(1, 11):
                            rev[f"tp{i}"] = None
                        rev["tp_stage"] = 0
                        rev["tg_message_id"] = -1 * int(orig["tg_message_id"])
                        if cfg.get("execution_type_rev"):
                            rev["execution_type"] = cfg["execution_type_rev"]
                        reverse_batch.append(rev)
                    enriched.extend(reverse_batch)

                for sig in enriched:
                    if not sig.get("action"):
                        continue
                    sid = insert_signal(sig)
                    logger.info(
                        "Signal id=%s canal=%s action=%s symbol=%s dir=%s magic=%s",
                        sid,
                        sig.get("channel_name"),
                        sig.get("action"),
                        sig.get("symbol"),
                        sig.get("direction"),
                        sig.get("magic"),
                    )

            except Exception as e:
                logger.exception("Error procesando mensaje: %s", e)

    def run_forever(self):
        init_schema()
        with self.client:
            self.client.loop.run_until_complete(self.start())
            logger.info("Escuchando (v1)...")
            self.client.run_until_disconnected()
