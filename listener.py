import asyncio
import logging
from copy import deepcopy
from datetime import datetime, timezone
from typing import Dict, Optional, List, Callable, Tuple

from telethon import TelegramClient, events, utils

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
    def __init__(
        self,
        api_id: int,
        api_hash: str,
        session_name: str = "tg_session_v1",
        progress_callback: Optional[Callable[[str], None]] = None,
    ):
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_name = session_name
        self.progress_callback = progress_callback
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.client: Optional[TelegramClient] = None

    def _emit(self, text: str):
        logger.info(text)
        if self.progress_callback:
            self.progress_callback(text)

    def _ensure_loop(self):
        try:
            loop = asyncio.get_running_loop()
            self.loop = loop
            return
        except RuntimeError:
            pass

        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                raise RuntimeError("Loop cerrado")
            self.loop = loop
        except RuntimeError:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)

    def _ensure_client(self):
        self._ensure_loop()
        if self.client is None:
            self.client = TelegramClient(
                self.session_name,
                self.api_id,
                self.api_hash,
            )

    async def _connect_client(self):
        self._ensure_client()

        if not self.client.is_connected():
            self._emit("INFO | TELETHON | Conectando cliente...")
            await self.client.connect()

        if not await self.client.is_user_authorized():
            raise RuntimeError(
                "La sesión de Telegram no está autorizada. "
                "Abre primero la aplicación en modo normal y valida la sesión."
            )

        self._emit("INFO | TELETHON | Cliente conectado y autorizado")

    async def _start_client_for_live(self):
        self._ensure_client()
        self._emit("INFO | LIVE | Arrancando cliente con start()")
        await self.client.start()
        self._emit("INFO | LIVE | Cliente iniciado")

    async def _disconnect_client(self):
        if self.client:
            try:
                if self.client.is_connected():
                    self._emit("INFO | TELETHON | Desconectando cliente...")
                    await self.client.disconnect()
                    self._emit("INFO | TELETHON | Cliente desconectado")
            except Exception:
                logger.exception("Error cerrando cliente Telethon")

    def _build_lookup_ids(self, raw_chat_id: Optional[int], peer_chat_id: Optional[int]) -> List[int]:
        ids: List[int] = []
        for value in (peer_chat_id, raw_chat_id):
            if value is None:
                continue
            value = int(value)
            if value not in ids:
                ids.append(value)

        if raw_chat_id is not None:
            raw_chat_id = int(raw_chat_id)
            neg100 = int(f"-100{abs(raw_chat_id)}")
            if neg100 not in ids:
                ids.append(neg100)

        if peer_chat_id is not None:
            peer_chat_id = int(peer_chat_id)
            peer_abs = abs(peer_chat_id)
            if peer_abs not in ids:
                ids.append(peer_abs)

        return ids

    def _get_cfg_for_ids(self, lookup_ids: List[int]) -> Optional[Dict]:
        for lookup_id in lookup_ids:
            cfg = get_channel_config_by_telegram_id(lookup_id)
            if cfg is not None:
                return cfg
        return None

    def _msg_ts(self, msg) -> int:
        if msg.date is None:
            return 0
        if msg.date.tzinfo is None:
            return int(msg.date.replace(tzinfo=timezone.utc).timestamp())
        return int(msg.date.timestamp())

    def _msg_dt_naive(self, msg) -> Optional[datetime]:
        if msg.date is None:
            return None
        if msg.date.tzinfo is not None:
            return msg.date.astimezone(timezone.utc).replace(tzinfo=None)
        return msg.date

    async def _extract_chat_info(self, msg) -> Tuple[Optional[int], Optional[int], str]:
        chat = await msg.get_chat()
        raw_chat_id = getattr(chat, "id", None)

        try:
            peer_chat_id = utils.get_peer_id(chat)
        except Exception:
            peer_chat_id = None

        title = getattr(chat, "title", None) or getattr(chat, "username", None) or str(raw_chat_id or peer_chat_id)
        return raw_chat_id, peer_chat_id, title

    def _store_raw_message(
        self,
        channel_id_for_db: int,
        channel_name: str,
        msg_id: int,
        msg_ts: int,
        text: str,
    ) -> int:
        raw_msg_id = insert_raw_message({
            "channel_id": str(channel_id_for_db),
            "channel_name": channel_name,
            "message_id": msg_id,
            "date": msg_ts,
            "text": text,
            "raw_json": None,
        })
        self._emit(
            f"INFO | DB | Raw message guardado raw_msg_id={raw_msg_id} "
            f"msg_id={msg_id} canal={channel_name} channel_id={channel_id_for_db}"
        )
        return raw_msg_id

    def _post_process_signals(
        self,
        cfg: Dict,
        raw_msg_id: int,
        msg_id: int,
        reply_to_id: Optional[int],
        signals_raw: List[Dict],
    ) -> List[Dict]:
        channel_name = cfg["channel_name"]
        enriched = []

        for sd in signals_raw:
            sd = dict(sd)
            sd["raw_message_id"] = raw_msg_id
            sd.setdefault("tg_message_id", msg_id)
            sd.setdefault("tg_reply_to_id", reply_to_id)
            sd["channel_name"] = channel_name

            action = sd.get("action", "")
            self._emit(
                f"INFO | PARSER | Señal candidata action={action} symbol={sd.get('symbol')} "
                f"dir={sd.get('direction')} magic={sd.get('magic')}"
            )

            if action == "SL_MOVE":
                sid = insert_sl_move(
                    sd.get("symbol"),
                    channel_name,
                    sd.get("sl"),
                    reply_to_id or 0,
                )
                self._emit(f"INFO | DB | SL_MOVE insertado id={sid} canal={channel_name} sl={sd.get('sl')}")
                continue

            if action in ("TP_DONE",):
                self._emit("INFO | PARSER | TP_DONE ignorado")
                continue

            if action == "OPEN" and sd.get("tp1"):
                tp_final = next(
                    (sd.get(f"tp{i}") for i in range(10, 0, -1) if sd.get(f"tp{i}") is not None),
                    None
                )
                if tp_final is not None:
                    sd["tp"] = tp_final
                    self._emit(f"INFO | PARSER | TP final consolidado={tp_final} msg_id={msg_id}")

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

                for k in ("tp1", "tp2", "tp3", "tp4", "tp5", "tp6", "tp7", "tp8", "tp9", "tp10"):
                    rev[k] = None
                rev["tp_stage"] = 0

                rev["tg_message_id"] = -1 * int(orig.get("tg_message_id") or 0)

                if cfg.get("execution_type_rev"):
                    rev["execution_type"] = cfg["execution_type_rev"]

                enriched.append(rev)
                self._emit(
                    f"INFO | PARSER | Señal inversa generada symbol={rev.get('symbol')} "
                    f"dir={rev.get('direction')} magic={rev.get('magic')}"
                )

        return enriched

    def _persist_signals(self, signals: List[Dict]) -> int:
        total = 0
        for sig in signals:
            if not sig.get("action"):
                continue

            sid = insert_signal(sig)
            self._emit(
                f"INFO | DB | Signal insertada id={sid} action={sig.get('action')} "
                f"symbol={sig.get('symbol')} dir={sig.get('direction')} magic={sig.get('magic')}"
            )
            total += 1
        return total

    async def _process_message_obj(
        self,
        msg,
        progress_callback: Optional[Callable[[str], None]] = None,
        ignore_unconfigured: bool = True,
    ) -> int:
        text = msg.message or ""
        if not text.strip():
            self._emit(f"INFO | MSG | Mensaje vacío msg_id={msg.id}")
            return 0

        raw_chat_id, peer_chat_id, fallback_title = await self._extract_chat_info(msg)
        lookup_ids = self._build_lookup_ids(raw_chat_id, peer_chat_id)

        self._emit(
            f"INFO | MSG | IDs candidatos msg_id={msg.id} raw_chat_id={raw_chat_id} "
            f"peer_chat_id={peer_chat_id} lookup_ids={lookup_ids}"
        )

        cfg = self._get_cfg_for_ids(lookup_ids)
        if cfg is None:
            self._emit(
                f"INFO | MSG | Canal no configurado msg_id={msg.id} "
                f"raw_chat_id={raw_chat_id} peer_chat_id={peer_chat_id}"
            )
            return 0

        channel_name = cfg["channel_name"]
        reply_to_id = getattr(msg, "reply_to_msg_id", None)
        msg_ts = self._msg_ts(msg)
        channel_id_for_db = int(peer_chat_id if peer_chat_id is not None else raw_chat_id)

        self._emit(f"INFO | MSG | Canal={channel_name} msg_id={msg.id} reply_to={reply_to_id}")
        self._emit(f"INFO | MSG | Texto={repr(text[:300])}")

        raw_msg_id = self._store_raw_message(
            channel_id_for_db=channel_id_for_db,
            channel_name=channel_name or fallback_title,
            msg_id=msg.id,
            msg_ts=msg_ts,
            text=text,
        )

        self._emit(f"INFO | PARSER | Parseando mensaje msg_id={msg.id} canal={channel_name}")

        signals_raw = parse_universal(
            text,
            cfg,
            tg_message_id=msg.id,
            tg_reply_to_id=reply_to_id,
        )

        self._emit(f"INFO | PARSER | parse_universal devolvió {len(signals_raw) if signals_raw else 0} resultado(s)")

        if not signals_raw:
            self._emit(f"INFO | PARSER | Sin señal msg_id={msg.id} canal={channel_name}")
            return 0

        enriched = self._post_process_signals(
            cfg=cfg,
            raw_msg_id=raw_msg_id,
            msg_id=msg.id,
            reply_to_id=reply_to_id,
            signals_raw=signals_raw,
        )

        inserted = self._persist_signals(enriched)

        if inserted == 0:
            self._emit(f"INFO | PARSER | No se insertaron señales finales msg_id={msg.id}")
        else:
            self._emit(f"INFO | PARSER | Señales finales insertadas={inserted} msg_id={msg.id}")

        if progress_callback and inserted:
            progress_callback(f"INFO | REPLAY | {channel_name}: msg {msg.id} -> {inserted} señal(es)")

        return inserted

    async def _async_start_live(self):
        await self._start_client_for_live()
        self._emit("INFO | LIVE | Cliente Telethon iniciado")
        self._register_handlers()

    def _register_handlers(self):
        @self.client.on(events.NewMessage())
        async def handler(event):
            try:
                await self._process_message_obj(event.message, ignore_unconfigured=False)
            except Exception as e:
                self._emit(f"ERROR | LIVE | Error procesando mensaje: {e}")
                logger.exception("Error procesando mensaje live: %s", e)

    def run_forever(self):
        init_schema()
        self._ensure_loop()
        self._ensure_client()

        self.loop.run_until_complete(self._async_start_live())
        self._emit("INFO | LIVE | Escuchando mensajes...")
        self.client.run_until_disconnected()

    async def _async_run_replay(
        self,
        selected_channel_ids: List[int],
        date_from: datetime,
        date_to: Optional[datetime] = None,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> int:
        await self._connect_client()

        total_messages = 0
        total_inserted = 0

        for channel_id in selected_channel_ids:
            if progress_callback:
                progress_callback(f"INFO | REPLAY | Replay canal {channel_id}...")

            cfg = get_channel_config_by_telegram_id(channel_id)
            channel_name = cfg["channel_name"] if cfg else str(channel_id)

            try:
                async for msg in self.client.iter_messages(channel_id, reverse=True):
                    msg_dt = self._msg_dt_naive(msg)
                    if msg_dt is None:
                        continue

                    if msg_dt < date_from:
                        continue

                    if date_to and msg_dt > date_to:
                        continue

                    total_messages += 1
                    self._emit(f"INFO | REPLAY | Procesando canal={channel_name} msg_id={msg.id} fecha={msg_dt}")

                    inserted = await self._process_message_obj(
                        msg,
                        progress_callback=progress_callback,
                        ignore_unconfigured=True,
                    )
                    total_inserted += inserted

                    if progress_callback and total_messages % 25 == 0:
                        progress_callback(
                            f"INFO | REPLAY | [{channel_name}] mensajes={total_messages} señales={total_inserted}"
                        )

            except Exception:
                logger.exception("Error en replay del canal %s", channel_id)
                raise

        await self._disconnect_client()

        if progress_callback:
            progress_callback(
                f"INFO | REPLAY | Replay completado. Mensajes={total_messages}, señales={total_inserted}"
            )

        return total_messages

    def run_replay(
        self,
        selected_channel_ids: List[int],
        date_from: datetime,
        date_to: Optional[datetime] = None,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> int:
        init_schema()
        self._ensure_loop()
        self._ensure_client()

        try:
            return self.loop.run_until_complete(
                self._async_run_replay(
                    selected_channel_ids=selected_channel_ids,
                    date_from=date_from,
                    date_to=date_to,
                    progress_callback=progress_callback,
                )
            )
        finally:
            try:
                if self.client and self.client.is_connected():
                    self.loop.run_until_complete(self._disconnect_client())
            except Exception:
                logger.exception("Error cerrando cliente tras replay")

    def close(self):
        try:
            if self.client and self.loop and not self.loop.is_closed():
                if self.client.is_connected():
                    self.loop.run_until_complete(self._disconnect_client())
        except Exception:
            logger.exception("Error en close()")
