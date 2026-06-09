import re
import time
from typing import Any, Dict, List, Optional
from .utils import normalize_text

OPEN_HEADER_RE = re.compile(
    r"(?P<symbol>[A-Z]{2,10}(?:USD|EUR|GBP|JPY|CHF|CAD|AUD|NZD|XAU|XAG)?)"
    r"\s+(?P<direction>BUY|SELL|COMPRAR|VENDER|VENTA)(?:\s+(?P<order_type>LIMIT|STOP|NOW))?",
    re.IGNORECASE,
)

ENTRY_RE = re.compile(
    r"(?:ENTRY|ENTER|PRICE|@|AT|ENTRADA\s+EN|ENTRADA)\s*[:\-]?\s*"
    r"(?P<e1>\d+(?:[.,]\d+)?)(?:\s*[-–]\s*(?P<e2>\d+(?:[.,]\d+)?))?",
    re.IGNORECASE,
)

SL_RE = re.compile(
    r"\bS\.?L\.?\s*[:\-]?\s*(?P<sl>\d+(?:[.,]\d+)?)",
    re.IGNORECASE,
)

TP_RE = re.compile(
    r"(?:\bT\.?P\.?\s*\d*|TOMA DE BENEFICIOS?\s*\d*|TAKE PROFIT\s*\d*|TOMA DE GANANCIAS?\s*\d*)\s*[:\-]?\s*(?P<tp>\d+(?:[.,]\d+)?)",
    re.IGNORECASE,
)

TP_HIT_RE = re.compile(
    r"(?:TP\s*(?P<n1>\d+)\s*(?:HIT|ALCANZADO)|(?P<n2>\d+)\s*TP\s*HIT|TP(?P<n3>\d+)\s*[- ]?HIT|(?P<n4>\d+)st\s*TP\s*hit)",
    re.IGNORECASE,
)

BE_RE = re.compile(
    r"\b(?:SET\s+)?(?:BREAK\s*EVEN|BREAKEVEN|BE|PUNTO\s+DE\s+EQUILIBRIO)\b",
    re.IGNORECASE,
)

CLOSE_ALL_RE = re.compile(
    r"\b(?:CLOSE\s+(?:ALL|IT|NOW|TRADE|POSITION)|CERRAR?\s+(?:TODO|POSICI[OÓ]N(?:ES)?))\b",
    re.IGNORECASE,
)

CLOSE_RE = re.compile(
    r"\b(?:CLOSE|CERRAR?|CIERRA|CERRAD)\b",
    re.IGNORECASE,
)

CANCEL_RE = re.compile(
    r"\b(?:CANCEL(?:LED)?|INVALID(?:ATE)?|ANULAD[AO]|ELIMINANDO\s+LA\s+ORDEN)\b",
    re.IGNORECASE,
)

SL_MOVE_RE = re.compile(
    r"\b(?:MOVE|MOVER?|SET|SUBO|MUEVE)\s+(?:S\.?L\.?|STOP(?:-LOSS)?|STOP LOSS)?\s*(?:TO|A)?\s*(?P<sl>\d+(?:[.,]\d+)?)",
    re.IGNORECASE,
)

SL_TO_BE_RE = re.compile(
    r"\bsubo\s+(?:el\s+)?sl\s+a(?:l)?\s+be\b|\bsl\s+a\s+be\b|mueve\s+tu\s+stop-loss.*punto\s+de\s+equilibrio",
    re.IGNORECASE,
)

COMPRO_RE = re.compile(
    r"\bcompro\s+(?P<symbol>[A-Za-z]{2,10})\s+en\s+(?P<price>\d+(?:[.,]\d+)?)",
    re.IGNORECASE,
)

VENDO_RE = re.compile(
    r"\bvendo\s+(?P<symbol>[A-Za-z]{2,10})\s+en\s+(?P<price>\d+(?:[.,]\d+)?|breakeven|break\s*even|be)\b",
    re.IGNORECASE,
)

GOLD_ALIAS_RE = re.compile(r"\bgold\b|\boro\b", re.IGNORECASE)

TP_FINAL_RE = re.compile(
    r"Tp\s+final\s+(?P<tp>\d+(?:[.,]\d+)?)",
    re.IGNORECASE,
)

GOLD_NOW_RE = re.compile(
    r"(?:(?P<dir1>buy|sell)\s+gold\s+now|gold\s+(?P<dir2>buy|sell)\s+now|\d+\s+gold\s+(?P<dir3>buy|sell)\s+now)",
    re.IGNORECASE,
)

TP_PRICE_UPDATE_RE = re.compile(
    r"(?P<price>\d+(?:[.,]\d+)?)\s+tp(?P<n>\d+)\*?",
    re.IGNORECASE,
)

SPANISH_OPEN_RE = re.compile(
    r"\b(?P<direction>COMPRAR|VENTA|VENDER)\s+(?P<symbol>[A-Za-z]{2,10})",
    re.IGNORECASE,
)

class _ChannelState:
    TIMEOUT = 4 * 3600

    def __init__(self):
        self.last_open: Optional[Dict] = None
        self.last_ts: float = 0.0

    def store(self, sig: Dict):
        self.last_open = sig
        self.last_ts = time.time()

    def get(self) -> Optional[Dict]:
        if self.last_open and (time.time() - self.last_ts) < self.TIMEOUT:
            return self.last_open
        self.last_open = None
        return None

    def clear(self):
        self.last_open = None

_channel_state: Dict[int, _ChannelState] = {}

def _get_state(tid: int) -> _ChannelState:
    if tid not in _channel_state:
        _channel_state[tid] = _ChannelState()
    return _channel_state[tid]

def _f(s: Optional[str]) -> Optional[float]:
    return float(s.replace(",", ".")) if s else None

def _sym(raw: str) -> str:
    aliases = {
        "GOLD": "XAUUSD",
        "ORO": "XAUUSD",
        "SILVER": "XAGUSD",
        "OIL": "USOIL",
        "BRENT": "UKOIL",
    }
    r = raw.upper().strip()
    return aliases.get(r, r)

def _dir(raw: str) -> str:
    r = raw.upper().strip()
    if r in ("COMPRAR",):
        return "BUY"
    if r in ("VENDER", "VENTA"):
        return "SELL"
    return r

def _open(cfg, symbol, direction, sl, tps, emin=None, emax=None, exec_type=None, tg_id=None):
    sig = {
        "channel_name": cfg["channel_name"],
        "magic": cfg["magic"],
        "symbol": symbol,
        "action": "OPEN",
        "direction": _dir(direction),
        "risk_pct": cfg.get("risk_pct", 2.0),
        "volume_hint": None,
        "sl": sl,
        "tp": tps[0] if tps else None,
        "entry_min": emin,
        "entry_max": emax,
        "created_at": int(time.time()),
        "status": "PENDING",
        "execution_type": exec_type or cfg.get("execution_type", "PENDING"),
        "tg_message_id": tg_id,
    }
    for i, v in enumerate(tps[:10]):
        sig[f"tp{i+1}"] = v
    return sig

def _mgmt(cfg, action, symbol=None, sl=None, tp=None, tp_label=None, tg_id=None, tg_reply=None):
    return {
        "channel_name": cfg["channel_name"],
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

def parse_universal(
    text: str,
    channel_cfg: Dict[str, Any],
    tg_message_id: Optional[int] = None,
    tg_reply_to_id: Optional[int] = None,
) -> Optional[List[Dict[str, Any]]]:
    if not text or not text.strip():
        return None

    norm = normalize_text(text)
    tid = channel_cfg.get("telegram_id", 0)
    st = _get_state(tid)

    m = COMPRO_RE.search(norm)
    if m:
        sym = _sym(m.group("symbol"))
        entry = _f(m.group("price"))
        sl = _f(SL_RE.search(norm).group("sl")) if SL_RE.search(norm) else 0.0
        tps = [_f(x.group("tp")) for x in TP_RE.finditer(norm) if x.group("tp")]
        sig = _open(channel_cfg, sym, "BUY", sl, tps, entry, entry, tg_id=tg_message_id)
        st.store(sig)
        return [sig]

    m = VENDO_RE.search(norm)
    if m:
        ps = m.group("price").lower()
        prev = st.get()
        cp = (prev.get("entry_min") if prev else 0.0) if re.match(r"(breakeven|break\s*even|be)$", ps) else _f(ps)
        st.clear()
        return [
            _mgmt(
                channel_cfg,
                "CLOSE",
                symbol=_sym(m.group("symbol")),
                tp=cp,
                tg_id=tg_message_id,
                tg_reply=tg_reply_to_id,
            )
        ]

    m = SPANISH_OPEN_RE.search(norm)
    if m:
        sym = _sym(m.group("symbol"))
        dirn = _dir(m.group("direction"))
        tps = [_f(x.group("tp")) for x in TP_RE.finditer(norm) if x.group("tp")]
        msl = SL_RE.search(norm)
        me = ENTRY_RE.search(norm)
        emin = emax = None

        if me:
            e1, e2 = _f(me.group("e1")), _f(me.group("e2"))
            if e2 is not None:
                emin, emax = sorted([e1, e2])
            else:
                emin = emax = e1

        sl = _f(msl.group("sl")) if msl else None
        if sl is not None or tps:
            sig = _open(channel_cfg, sym, dirn, sl, tps, emin, emax, tg_id=tg_message_id)
            st.store(sig)
            return [sig]

    m = OPEN_HEADER_RE.search(norm)
    if m:
        raw = m.group("symbol")
        if GOLD_ALIAS_RE.search(raw):
            raw = "XAUUSD"

        sym = _sym(raw)
        dirn = _dir(m.group("direction"))
        msl = SL_RE.search(norm)
        tps = [_f(x.group("tp")) for x in TP_RE.finditer(norm) if x.group("tp")]
        me = ENTRY_RE.search(norm)
        emin = emax = None

        if me:
            e1, e2 = _f(me.group("e1")), _f(me.group("e2"))
            if e2 is not None:
                emin, emax = sorted([e1, e2])
            else:
                emin = emax = e1

        sl = _f(msl.group("sl")) if msl else None
        if sl is not None or tps:
            sig = _open(channel_cfg, sym, dirn, sl, tps, emin, emax, tg_id=tg_message_id)
            st.store(sig)
            return [sig]

    m = GOLD_NOW_RE.search(norm)
    if m:
        dirn = _dir((m.group("dir1") or m.group("dir2") or m.group("dir3")).upper())
        msl = SL_RE.search(norm)
        tps = [_f(x.group("tp")) for x in TP_RE.finditer(norm) if x.group("tp")]
        em = re.search(r"(?P<p>\d{4,5}(?:\.\d+)?)\s+gold", norm, re.IGNORECASE)
        ev = _f(em.group("p")) if em else None
        sl = _f(msl.group("sl")) if msl else None

        sig = _open(channel_cfg, "XAUUSD", dirn, sl, tps, ev, ev, tg_id=tg_message_id)
        st.store(sig)
        return [sig]

    if CLOSE_ALL_RE.search(norm):
        prev = st.get()
        sym = prev.get("symbol") if prev else None
        st.clear()
        return [_mgmt(channel_cfg, "CLOSE_ALL", symbol=sym, tg_id=tg_message_id)]

    if CLOSE_RE.search(norm):
        prev = st.get()
        sym = prev.get("symbol") if prev else None
        ms = re.search(r"\b([A-Z]{6,}|GOLD|SILVER|XAUUSD|XAGUSD)\b", norm, re.IGNORECASE)
        if ms:
            sym = _sym(ms.group(1))
        st.clear()
        return [_mgmt(channel_cfg, "CLOSE", symbol=sym, tg_id=tg_message_id)]

    if CANCEL_RE.search(norm):
        prev = st.get()
        sym = prev.get("symbol") if prev else None
        return [_mgmt(channel_cfg, "CANCEL", symbol=sym, tg_id=tg_message_id)]

    m = TP_HIT_RE.search(norm)
    if m:
        tpnum = int(m.group("n1") or m.group("n2") or m.group("n3") or 1)
        prev = st.get()
        sym = prev.get("symbol") if prev else None

        sigs = [
            _mgmt(
                channel_cfg,
                "PARTIAL",
                symbol=sym,
                tp_label=f"TP{tpnum}",
                tg_id=tg_message_id,
            )
        ]

        if prev:
            if tpnum == 1 and (BE_RE.search(norm) or "mueve tu stop-loss" in norm.lower()):
                sigs.append(_mgmt(channel_cfg, "BE", symbol=sym, tg_id=tg_message_id))
            elif tpnum == 2 and prev.get("tp1"):
                sigs.append(_mgmt(channel_cfg, "SL_MOVE", symbol=sym, sl=prev["tp1"], tg_id=tg_message_id))
            elif tpnum == 3 and prev.get("tp2"):
                sigs.append(_mgmt(channel_cfg, "SL_MOVE", symbol=sym, sl=prev["tp2"], tg_id=tg_message_id))

        return sigs

    if BE_RE.search(norm) or SL_TO_BE_RE.search(norm):
        prev = st.get()
        sym = prev.get("symbol") if prev else None
        return [_mgmt(channel_cfg, "BE", symbol=sym, tg_id=tg_message_id)]

    m = SL_MOVE_RE.search(norm)
    if m:
        prev = st.get()
        sym = prev.get("symbol") if prev else None
        return [
            _mgmt(
                channel_cfg,
                "SL_MOVE",
                symbol=sym,
                sl=_f(m.group("sl")),
                tg_id=tg_message_id,
                tg_reply=tg_reply_to_id,
            )
        ]

    if tg_reply_to_id:
        msl = SL_RE.search(norm)
        if msl:
            prev = st.get()
            sym = prev.get("symbol") if prev else None
            return [
                _mgmt(
                    channel_cfg,
                    "SL_MOVE",
                    symbol=sym,
                    sl=_f(msl.group("sl")),
                    tg_id=tg_message_id,
                    tg_reply=tg_reply_to_id,
                )
            ]

    m = TP_FINAL_RE.search(norm)
    if m:
        prev = st.get()
        sym = prev.get("symbol") if prev else None
        return [
            _mgmt(
                channel_cfg,
                "TP_UPDATE",
                symbol=sym,
                tp=_f(m.group("tp")),
                tp_label="FINAL",
                tg_id=tg_message_id,
            )
        ]

    m = TP_PRICE_UPDATE_RE.search(norm)
    if m:
        prev = st.get()
        sym = prev.get("symbol") if prev else None
        return [
            _mgmt(
                channel_cfg,
                "TP_UPDATE",
                symbol=sym,
                tp=_f(m.group("price")),
                tp_label=f"TP{m.group('n')}",
                tg_id=tg_message_id,
            )
        ]

    return None
