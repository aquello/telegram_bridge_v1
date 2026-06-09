import logging
from copy import deepcopy
from typing import Optional

from telethon import TelegramClient, events

from .db import (
    get_channel_config_by_telegram_id,
    init_schema,
    insert_raw_message,
    insert_signal,
    insert_sl_move,
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

    def _register_handlers(self):
        @self.client.on(events.NewMessage())
        async def handler(event):
            try:
                chat = await event.get_chat()
                raw_id = chat.id

                cfg = get_channel_config_by_telegram_id(raw_id)
                if cfg is None:
                    cfg = get_channel_config_by_telegram_id(-(1_000_000_000_000 + abs(raw_id)))

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

                logger.info("[PARSE] canal=%s id=%s text=%r", channel_name, msg.id, text[:120])

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
                    sd = dict(sd)
                    sd["raw_message_id"] = raw_msg_id
                    sd.setdefault("tg_message_id", msg.id)
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

                if cfg.get("enable_reverse", 0):
                    for orig in list(enriched):
                        if orig.get("action") != "OPEN":
                            continue
                        if not orig.get("symbol") or not orig.get("direction"):
                            continue

                        rev = deepcopy(orig)

                        if cfg.get("magic_reverse"):
                            rev["magic"] = cfg["magic_reverse"]

                        rev["direction"] = "SELL" if orig["direction"] == "BUY" else "BUY"

                        sl, tp = orig.get("sl"), orig.get("tp")
                        if sl is not None and tp is not None:
                            rev["sl"], rev["tp"] = tp, sl

                        for k in ("tp1", "tp2", "tp3", "tp4", "tp5", "tp_stage"):
                            rev[k] = 0

                        rev["tg_message_id"] = -1 * orig["tg_message_id"]

                        if cfg.get("execution_type_rev"):
                            rev["execution_type"] = cfg["execution_type_rev"]

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

            except Exception as e:
                logger.exception("Error procesando mensaje: %s", e)

    def run_forever(self):
        init_schema()
        with self.client:
            self.client.loop.run_until_complete(self.start())
            logger.info("Escuchando (v1)...")
            self.client.run_until_disconnected()
