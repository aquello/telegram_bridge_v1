"""
universal_parser.py – Parser unificado con estado robusto para telegram_bridge_v1

Mejoras:
- Estado en memoria por telegram_id + símbolo, con soporte multi-open.
- Correlación por reply_to, por símbolo explícito y por fallback al último OPEN.
- Restauración desde BD de múltiples OPEN activas.
- Regex más conservadores para reducir falsos positivos.
"""

import re
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional

from .utils import normalize_text

OPEN_HEADER_RE = re.compile(
    r"(?P<symbol>[A-Z]{2,10}(?:USD|EUR|GBP|JPY|CHF|CAD|AUD|NZD|XAU|XAG)?)"
    r"\s+(?P<direction>BUY|SELL)(?:\s+(?P<order_type>LIMIT|STOP|NOW))?",
    re.IGNORECASE,
)
ENTRY_RE = re.compile(
    r"(?:ENTRY|ENTER|PRICE|@|AT)\s*[:\-]?\s*"
    r"(?P<e1>\d+(?:[.,]\d+)?)(?:\s*[-–]\s*(?P<e2>\d+(?:[.,]\d+)?))?",
    re.IGNORECASE,
)
SL_RE = re.compile(r"\bS\.?L\.?\s*[:\-]?\s*(?P<sl>\d+(?:[.,]\d+)?)\b", re.IGNORECASE)
TP_RE = re.compile(r"\bT\.?P\.?\s*\d*\s*[:\-]?\s*(?P<tp>\d+(?:[.,]\d+)?)\b", re.IGNORECASE)
TP_HIT_RE = re.compile(r"(?:\bTP\s*(?P<n1>\d+)\s+HIT\b|\b(?P<n2>\d+)\s+TP\s+HIT\b)", re.IGNORECASE)
BE_RE = re.compile(r"\b(?:SET\s+)?(?:BREAK\s*EVEN|BREAKEVEN)\b|\bSL\s+TO\s+BE\b", re.IGNORECASE)
CLOSE_ALL_RE = re.compile(r"\b(?:CLOSE\s+ALL|CLOSE\s+NOW|CERRAR\s+TODO)\b", re.IGNORECASE)
CLOSE_RE = re.compile(r"\b(?:CLOSE(?:\s+TRADE|\s+POSITION)?|CERRAR)\b", re.IGNORECASE)
CANCEL_RE = re.compile(r"\b(?:CANCEL(?:LED)?|INVALID(?:ATE)?|ANULAD[AO])\b", re.IGNORECASE)
SL_MOVE_RE = re.compile(r"\b(?:MOVE|MOVER?|SET)\s+S\.?L\.?\s+(?:TO|A)\s+(?P<sl>\d+(?:[.,]\d+)?)\b", re.IGNORECASE)
SL_TO_BE_RE = re.compile(r"\bsubo\s+(?:el\s+)?sl\s+a(?:l)?\s+be\b|\bsl\s+a\s+be\b", re.IGNORECASE)
COMPRO_RE = re.compile(r"\bcompro\s+(?P<symbol>[A-Za-z]{2,10})\s+en\s+(?P<price>\d+(?:[.,]\d+)?)\b", re.IGNORECASE)
VENDO_RE = re.compile(
    r"\bvendo\s+(?P<symbol>[A-Za-z]{2,10})\s+en\s+(?P<price>\d+(?:[.,]\d+)?|breakeven|break\s*even|be)\b",
    re.IGNORECASE,
)
TP_FINAL_RE = re.compile(r"\bTp\s+final\s+(?P<tp>\d+(?:[.,]\d+)?)\b", re.IGNORECASE)
TP_PRICE_UPDATE_RE = re.compile(r"\b(?P<price>\d+(?:[.,]\d+)?)\s+tp(?P<n>\d+)\*?\b", re.IGNORECASE)
GOLD_NOW_RE = re.compile(
    r"(?:(?P<dir1>buy|sell)\s+gold\s+now|gold\s+(?P<dir2>buy|sell)\s+now|"
    r"\d+\s+gold\s+(?P<dir3>buy|sell)\s+now)",
    re.IGNORECASE,
)


class _ChannelState:
    TIMEOUT = 12 * 3600

    def __init__(self):
        self.by_symbol: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self.by_message_id: Dict[int, Dict[str, Any]] = {}

    def _prune(self):
        now = time.time()
        for symbol in list(self.by_symbol.keys()):
            kept = []
            for sig in self.by_symbol[symbol]:
                created = float(sig.get("created_at", 0))
                if now - created < self.TIMEOUT:
                    kept.append(sig)
                else:
                    mid = sig.get("tg_message_id")
                    if mid in self.by_message_id:
                        del self.by_message_id[mid]
            if kept:
                self.by_symbol[symbol] = kept
            else:
                del self.by_symbol[symbol]

    def store(self, sig: Dict[str, Any]):
        self._prune()
        symbol = (sig.get("symbol") or "").upper()
        if not symbol:
            return
        self.by_symbol[symbol].append(sig)
        mid = sig.get("tg_message_id")
        if mid:
            self.by_message_id[mid] = sig

    def clear_symbol(self, symbol: Optional[str]):
        self._prune()
        if not symbol:
            return
        symbol = symbol.upper()
        for sig in self.by_symbol.get(symbol, []):
            mid = sig.get("tg_message_id")
            if mid in self.by_message_id:
                del self.by_message_id[mid]
        self.by_symbol.pop(symbol, None)

    def clear_all(self):
        self.by_symbol.clear()
        self.by_message_id.clear()

    def get_by_reply(self, reply_id: Optional[int]) -> Optional[Dict[str, Any]]:
        self._prune()
        if not reply_id:
            return None
        return self.by_message_id.get(reply_id)

    def get_last_for_symbol(self, symbol: Optional[str]) -> Optional[Dict[str, Any]]:
        self._prune()
        if not symbol:
            return self.get_last_any()
        symbol = symbol.upper()
        arr = self.by_symbol.get(symbol, [])
        return arr[-1] if arr else None

    def get_last_any(self) -> Optional[Dict[str, Any]]:
        self._prune()
        candidates = []
        for arr in self.by_symbol.values():
            candidates.extend(arr)
        if not candidates:
            return None
        candidates.sort(key=lambda x: (x.get("created_at", 0), x.get("tg_message_id", 0)))
        return candidates[-1]


_channel_state: Dict[int, _ChannelState] = {}


def _get_state(tid: int) -> _ChannelState:
    if tid not in _channel_state:
        _channel_state[tid] = _ChannelState()
    return _channel_state[tid]


def _f(s: Optional[str]) -> Optional[float]:
    return float(s.replace(",", ".")) if s else None


def _sym(raw: str) -> str:
    aliases = {"GOLD": "XAUUSD", "SILVER": "XAGUSD", "OIL": "USOIL", "BRENT": "UKOIL"}
    return aliases.get(raw.upper().strip(), raw.upper().strip())


def _extract_symbol(text: str) -> Optional[str]:
    m = re.search(r"\b([A-Z]{6,}|GOLD|SILVER|XAUUSD|XAGUSD|USOIL|UKOIL)\b", text, re.IGNORECASE)
    return _sym(m.group(1)) if m else None


def _pick_context(state: _ChannelState, text: str, tg_reply_to_id: Optional[int]) -> Optional[Dict[str, Any]]:
    prev = state.get_by_reply(tg_reply_to_id)
    if prev:
        return prev
    symbol = _extract_symbol(text)
    if symbol:
        prev = state.get_last_for_symbol(symbol)
        if prev:
            return prev
    return state.get_last_any()


def _build_open(cfg, symbol, direction, sl, tps, entry_min=None, entry_max=None, exec_type=None, tg_id=None) -> Dict[str, Any]:
    sig = {
        "channel_name": cfg["channel_name"],
        "telegram_id": cfg["telegram_id"],
        "magic": cfg["magic"],
        "symbol": symbol,
        "action": "OPEN",
        "direction": direction.upper(),
        "risk_pct": cfg.get("risk_pct", 2.0),
        "volume_hint": None,
        "sl": sl,
        "tp": tps[-1] if tps else None,
        "entry_min": entry_min,
        "entry_max": entry_max,
        "created_at": int(time.time()),
        "status": "PENDING",
        "execution_type": exec_type or cfg.get("execution_type", "PENDING"),
        "tg_message_id": tg_id,
    }
    for i, v in enumerate(tps[:10], start=1):
        sig[f"tp{i}"] = v
    return sig


def _build_mgmt(cfg, action, symbol=None, sl=None, tp=None, tp_label=None, tg_id=None, tg_reply=None) -> Dict[str, Any]:
    return {
        "channel_name": cfg["channel_name"],
        "telegram_id": cfg["telegram_id"],
        "magic": cfg["magic"],
        "symbol": symbol,
        "action": action,
        "direction": None,
        "risk_pct": None,
        "volume_hint": None,
        "sl": sl,
        "tp": tp,
        "tp_label": tp_label,
        "entry_min": None,
        "entry_max": None,
        "created_at": int(time.time()),
        "status": "PENDING",
        "tg_message_id": tg_id,
        "tg_reply_to_id": tg_reply,
    }


def restore_state_from_db(open_signals: List[Dict[str, Any]]) -> None:
    for sig in open_signals:
        tid = sig.get("telegram_id")
        if not tid:
            continue
        _get_state(tid).store(sig)


def parse_universal(text: str, channel_cfg: Dict[str, Any], tg_message_id: Optional[int] = None, tg_reply_to_id: Optional[int] = None) -> Optional[List[Dict[str, Any]]]:
    if not text or not text.strip():
        return None

    norm = normalize_text(text)
    tid = channel_cfg.get("telegram_id", 0)
    state = _get_state(tid)

    m = COMPRO_RE.search(norm)
    if m:
        symbol = _sym(m.group("symbol"))
        entry = _f(m.group("price"))
        slm = SL_RE.search(norm)
        sl = _f(slm.group("sl")) if slm else None
        tps = [_f(x.group("tp")) for x in TP_RE.finditer(norm) if x.group("tp")]
        sig = _build_open(channel_cfg, symbol, "BUY", sl, tps, entry, entry, tg_id=tg_message_id)
        state.store(sig)
        return [sig]

    m = VENDO_RE.search(norm)
    if m:
        ctx = _pick_context(state, norm, tg_reply_to_id)
        symbol = _sym(m.group("symbol"))
        price_token = m.group("price").lower()
        price = (ctx.get("entry_min") if ctx else None) if re.match(r"(breakeven|break\s*even|be)$", price_token) else _f(price_token)
        state.clear_symbol(symbol)
        return [_build_mgmt(channel_cfg, "CLOSE", symbol=symbol, tp=price, tg_id=tg_message_id, tg_reply=tg_reply_to_id)]

    m = OPEN_HEADER_RE.search(norm)
    if m:
        symbol = _sym(m.group("symbol"))
        direction = m.group("direction").upper()
        slm = SL_RE.search(norm)
        tps = [_f(x.group("tp")) for x in TP_RE.finditer(norm) if x.group("tp")]
        em = ENTRY_RE.search(norm)
        entry_min = entry_max = None
        if em:
            e1, e2 = _f(em.group("e1")), _f(em.group("e2"))
            if e2 is not None:
                entry_min, entry_max = sorted([e1, e2])
            else:
                entry_min = entry_max = e1
        sl = _f(slm.group("sl")) if slm else None
        if (sl is not None or tps) and (entry_min is not None or " now" in norm.lower() or m.group("order_type")):
            sig = _build_open(channel_cfg, symbol, direction, sl, tps, entry_min, entry_max, tg_id=tg_message_id)
            state.store(sig)
            return [sig]

    m = GOLD_NOW_RE.search(norm)
    if m:
        direction = (m.group("dir1") or m.group("dir2") or m.group("dir3")).upper()
        slm = SL_RE.search(norm)
        tps = [_f(x.group("tp")) for x in TP_RE.finditer(norm) if x.group("tp")]
        em = re.search(r"(?P<p>\d{4,5}(?:\.\d+)?)\s+gold", norm, re.IGNORECASE)
        entry = _f(em.group("p")) if em else None
        sl = _f(slm.group("sl")) if slm else None
        sig = _build_open(channel_cfg, "XAUUSD", direction, sl, tps, entry, entry, tg_id=tg_message_id)
        state.store(sig)
        return [sig]

    if CLOSE_ALL_RE.search(norm):
        ctx = _pick_context(state, norm, tg_reply_to_id)
        symbol = ctx.get("symbol") if ctx else None
        state.clear_all()
        return [_build_mgmt(channel_cfg, "CLOSE_ALL", symbol=symbol, tg_id=tg_message_id)]

    if CANCEL_RE.search(norm):
        ctx = _pick_context(state, norm, tg_reply_to_id)
        symbol = ctx.get("symbol") if ctx else None
        return [_build_mgmt(channel_cfg, "CANCEL", symbol=symbol, tg_id=tg_message_id, tg_reply=tg_reply_to_id)]

    m = TP_HIT_RE.search(norm)
    if m:
        tpnum = int(m.group("n1") or m.group("n2"))
        ctx = _pick_context(state, norm, tg_reply_to_id)
        symbol = ctx.get("symbol") if ctx else None
        signals = [_build_mgmt(channel_cfg, "PARTIAL", symbol=symbol, tp_label=f"TP{tpnum}", tg_id=tg_message_id, tg_reply=tg_reply_to_id)]
        if ctx:
            if tpnum == 1 and (BE_RE.search(norm) or SL_TO_BE_RE.search(norm)):
                signals.append(_build_mgmt(channel_cfg, "BE", symbol=symbol, tg_id=tg_message_id, tg_reply=tg_reply_to_id))
            elif tpnum == 2 and ctx.get("tp1"):
                signals.append(_build_mgmt(channel_cfg, "SL_MOVE", symbol=symbol, sl=ctx.get("tp1"), tg_id=tg_message_id, tg_reply=tg_reply_to_id))
            elif tpnum == 3 and ctx.get("tp2"):
                signals.append(_build_mgmt(channel_cfg, "SL_MOVE", symbol=symbol, sl=ctx.get("tp2"), tg_id=tg_message_id, tg_reply=tg_reply_to_id))
        return signals

    if BE_RE.search(norm) or SL_TO_BE_RE.search(norm):
        ctx = _pick_context(state, norm, tg_reply_to_id)
        symbol = ctx.get("symbol") if ctx else None
        return [_build_mgmt(channel_cfg, "BE", symbol=symbol, tg_id=tg_message_id, tg_reply=tg_reply_to_id)]

    m = SL_MOVE_RE.search(norm)
    if m:
        ctx = _pick_context(state, norm, tg_reply_to_id)
        symbol = ctx.get("symbol") if ctx else None
        return [_build_mgmt(channel_cfg, "SL_MOVE", symbol=symbol, sl=_f(m.group("sl")), tg_id=tg_message_id, tg_reply=tg_reply_to_id)]

    if tg_reply_to_id:
        slm = SL_RE.search(norm)
        if slm and not OPEN_HEADER_RE.search(norm):
            ctx = _pick_context(state, norm, tg_reply_to_id)
            symbol = ctx.get("symbol") if ctx else None
            return [_build_mgmt(channel_cfg, "SL_MOVE", symbol=symbol, sl=_f(slm.group("sl")), tg_id=tg_message_id, tg_reply=tg_reply_to_id)]

    m = TP_FINAL_RE.search(norm)
    if m:
        ctx = _pick_context(state, norm, tg_reply_to_id)
        symbol = ctx.get("symbol") if ctx else None
        return [_build_mgmt(channel_cfg, "TP_UPDATE", symbol=symbol, tp=_f(m.group("tp")), tp_label="FINAL", tg_id=tg_message_id, tg_reply=tg_reply_to_id)]

    m = TP_PRICE_UPDATE_RE.search(norm)
    if m:
        ctx = _pick_context(state, norm, tg_reply_to_id)
        symbol = ctx.get("symbol") if ctx else None
        return [_build_mgmt(channel_cfg, "TP_UPDATE", symbol=symbol, tp=_f(m.group("price")), tp_label=f"TP{m.group('n')}", tg_id=tg_message_id, tg_reply=tg_reply_to_id)]

    if CLOSE_RE.search(norm):
        ctx = _pick_context(state, norm, tg_reply_to_id)
        symbol = _extract_symbol(norm) or (ctx.get("symbol") if ctx else None)
        if symbol:
            state.clear_symbol(symbol)
        return [_build_mgmt(channel_cfg, "CLOSE", symbol=symbol, tg_id=tg_message_id, tg_reply=tg_reply_to_id)]

    return None
