import os
import sqlite3
import time
from typing import Any, Dict, List, Optional


DB_PATH_REAL = r"C:\Users\AdmVps\AppData\Roaming\MetaQuotes\Terminal\Common\Files\signals.db"
DB_PATH_BT = os.path.join(os.path.dirname(DB_PATH_REAL), "signals_bt.db")
DB_PATH = DB_PATH_REAL


def _ensure_parent_dir(path: str):
    folder = os.path.dirname(os.path.abspath(path))
    if folder and not os.path.exists(folder):
        os.makedirs(folder, exist_ok=True)


def set_db_path(path: str):
    global DB_PATH
    if not path or not path.strip():
        raise ValueError("La ruta de BBDD no puede estar vacía.")
    abs_path = os.path.abspath(path.strip())
    _ensure_parent_dir(abs_path)
    DB_PATH = abs_path


def get_db_path() -> str:
    return DB_PATH


def use_backtest_db():
    global DB_PATH
    DB_PATH = DB_PATH_BT


def use_real_db():
    global DB_PATH
    DB_PATH = DB_PATH_REAL


def get_connection() -> sqlite3.Connection:
    _ensure_parent_dir(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


_SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS raw_messages (
    id INTEGER PRIMARY KEY,
    channel_id TEXT,
    channel_name TEXT,
    message_id INTEGER,
    date INTEGER,
    text TEXT,
    raw_json TEXT
);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY,
    raw_message_id INTEGER,
    channel_name TEXT,
    magic INTEGER,
    symbol TEXT,
    action TEXT,
    direction TEXT,
    risk_pct REAL,
    volume_hint REAL,
    sl REAL,
    tp REAL,
    created_at INTEGER,
    status TEXT,
    tg_message_id INTEGER,
    tg_reply_to_id INTEGER,
    tp_label TEXT,
    entry_min REAL,
    entry_max REAL,
    tp1 REAL,
    tp2 REAL,
    tp3 REAL,
    tp4 REAL,
    tp5 REAL,
    tp6 REAL,
    tp7 REAL,
    tp8 REAL,
    tp9 REAL,
    tp10 REAL,
    tp_stage INTEGER DEFAULT 0,
    execution_type TEXT DEFAULT 'PENDING',
    executed_by TEXT,
    executed_at INTEGER,
    error_msg TEXT,
    exported_mt4 INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS executions (
    id INTEGER PRIMARY KEY,
    signal_id INTEGER,
    ticket INTEGER,
    symbol TEXT,
    volume REAL,
    price_open REAL,
    price_close REAL,
    sl REAL,
    tp REAL,
    close_reason TEXT,
    profit REAL,
    time_open INTEGER,
    time_close INTEGER
);

CREATE TABLE IF NOT EXISTS channel_config (
    id INTEGER PRIMARY KEY,
    channel_name TEXT UNIQUE NOT NULL,
    telegram_id INTEGER UNIQUE NOT NULL,
    magic INTEGER NOT NULL,
    risk_pct REAL DEFAULT 2.0,
    execution_type TEXT DEFAULT 'PENDING',
    enabled INTEGER DEFAULT 1,
    enable_reverse INTEGER DEFAULT 0,
    magic_reverse INTEGER,
    execution_type_rev TEXT,
    created_at INTEGER,
    updated_at INTEGER,
    partial_tp_config TEXT
);

CREATE TABLE IF NOT EXISTS channel_brokers (
    id INTEGER PRIMARY KEY,
    channel_id INTEGER NOT NULL,
    broker_name TEXT NOT NULL,
    broker_magic INTEGER,
    enabled INTEGER DEFAULT 1,
    FOREIGN KEY (channel_id) REFERENCES channel_config(id)
);

CREATE INDEX IF NOT EXISTS idx_channel_config_telegram_id
ON channel_config(telegram_id);

CREATE INDEX IF NOT EXISTS idx_channel_config_enabled
ON channel_config(enabled);

CREATE INDEX IF NOT EXISTS idx_signals_channel_name
ON signals(channel_name);

CREATE UNIQUE INDEX IF NOT EXISTS idx_raw_messages_unique
ON raw_messages(channel_id, message_id);
"""

_MIGRATIONS = [
    "ALTER TABLE signals ADD COLUMN tp1 REAL",
    "ALTER TABLE signals ADD COLUMN tp2 REAL",
    "ALTER TABLE signals ADD COLUMN tp3 REAL",
    "ALTER TABLE signals ADD COLUMN tp4 REAL",
    "ALTER TABLE signals ADD COLUMN tp5 REAL",
    "ALTER TABLE signals ADD COLUMN tp6 REAL",
    "ALTER TABLE signals ADD COLUMN tp7 REAL",
    "ALTER TABLE signals ADD COLUMN tp8 REAL",
    "ALTER TABLE signals ADD COLUMN tp9 REAL",
    "ALTER TABLE signals ADD COLUMN tp10 REAL",
    "ALTER TABLE signals ADD COLUMN tp_stage INTEGER DEFAULT 0",
    "ALTER TABLE signals ADD COLUMN execution_type TEXT DEFAULT 'PENDING'",
    "ALTER TABLE signals ADD COLUMN executed_by TEXT",
    "ALTER TABLE signals ADD COLUMN executed_at INTEGER",
    "ALTER TABLE signals ADD COLUMN error_msg TEXT",
    "ALTER TABLE signals ADD COLUMN exported_mt4 INTEGER DEFAULT 0",
    "ALTER TABLE channel_config ADD COLUMN partial_tp_config TEXT",
    "ALTER TABLE channel_config ADD COLUMN enable_reverse INTEGER DEFAULT 0",
    "ALTER TABLE channel_config ADD COLUMN magic_reverse INTEGER",
    "ALTER TABLE channel_config ADD COLUMN execution_type_rev TEXT",
]


def init_schema():
    conn = get_connection()
    cur = conn.cursor()
    cur.executescript(_SCHEMA)

    for sql in _MIGRATIONS:
        try:
            cur.execute(sql)
        except sqlite3.OperationalError:
            pass

    conn.commit()
    conn.close()


def raw_message_exists(channel_id: str, message_id: int) -> bool:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM raw_messages WHERE channel_id=? AND message_id=? LIMIT 1",
        (channel_id, message_id),
    )
    row = cur.fetchone()
    conn.close()
    return row is not None


def insert_raw_message(data: Dict[str, Any]) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR IGNORE INTO raw_messages (
            channel_id, channel_name, message_id, date, text, raw_json
        ) VALUES (
            :channel_id, :channel_name, :message_id, :date, :text, :raw_json
        )
        """,
        data,
    )
    if cur.lastrowid:
        rid = cur.lastrowid
    else:
        cur.execute(
            "SELECT id FROM raw_messages WHERE channel_id=? AND message_id=?",
            (data["channel_id"], data["message_id"]),
        )
        row = cur.fetchone()
        rid = row["id"]
    conn.commit()
    conn.close()
    return rid


def insert_signal(data: Dict[str, Any]) -> int:
    conn = get_connection()
    data = dict(data)

    for k in (
        "risk_pct", "volume_hint", "entry_min", "entry_max", "magic",
        "tp_label", "error_msg", "tp", "sl", "tp1", "tp2", "tp3",
        "tp4", "tp5", "tp6", "tp7", "tp8", "tp9", "tp10"
    ):
        data.setdefault(k, None)

    data.setdefault("status", "PENDING")
    data.setdefault("tp_stage", 0)
    data.setdefault("action", None)
    data.setdefault("direction", None)
    data.setdefault("symbol", None)
    data.setdefault("execution_type", "MARKET")
    data.setdefault("created_at", int(time.time()))
    data.setdefault("tg_reply_to_id", None)

    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO signals (
            raw_message_id, channel_name, magic, symbol, action,
            direction, risk_pct, volume_hint, sl, tp, created_at, status,
            tg_message_id, tg_reply_to_id, tp_label, entry_min, entry_max,
            tp1, tp2, tp3, tp4, tp5, tp6, tp7, tp8, tp9, tp10,
            tp_stage, execution_type
        ) VALUES (
            :raw_message_id, :channel_name, :magic, :symbol, :action,
            :direction, :risk_pct, :volume_hint, :sl, :tp, :created_at, :status,
            :tg_message_id, :tg_reply_to_id, :tp_label, :entry_min, :entry_max,
            :tp1, :tp2, :tp3, :tp4, :tp5, :tp6, :tp7, :tp8, :tp9, :tp10,
            :tp_stage, :execution_type
        )
        """,
        data,
    )
    conn.commit()
    sid = cur.lastrowid
    conn.close()
    return sid


def insert_sl_move(symbol, channel_name, new_sl, tg_reply_to_id) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO signals (
            action, symbol, channel_name, sl, tg_reply_to_id, status
        ) VALUES (
            'SL_MOVE', :symbol, :channel_name, :sl, :tg_reply_to_id, 'PENDING'
        )
        """,
        {
            "symbol": symbol,
            "channel_name": channel_name,
            "sl": new_sl,
            "tg_reply_to_id": tg_reply_to_id,
        },
    )
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return rid


def get_channel_config_by_telegram_id(telegram_id: int) -> Optional[Dict]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM channel_config WHERE telegram_id=? AND enabled=1",
        (telegram_id,),
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_channel_configs() -> List[Dict]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM channel_config ORDER BY channel_name COLLATE NOCASE")
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_channel_config_map() -> Dict[int, Dict]:
    rows = get_all_channel_configs()
    out = {}
    for r in rows:
        try:
            out[int(r["telegram_id"])] = r
        except Exception:
            continue
    return out


def upsert_channel_config(
    *,
    channel_name: str,
    telegram_id: int,
    magic: int,
    enabled: int = 1,
    enable_reverse: int = 0,
    magic_reverse: Optional[int] = None,
    execution_type: str = "MARKET",
    execution_type_rev: Optional[str] = None,
    risk_pct: float = 2.0,
    partial_tp_config: Optional[str] = None,
) -> int:
    now_ts = int(time.time())
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        "SELECT id FROM channel_config WHERE telegram_id=?",
        (telegram_id,),
    )
    row = cur.fetchone()

    if row:
        cur.execute(
            """
            UPDATE channel_config
            SET
                channel_name=?,
                magic=?,
                risk_pct=?,
                execution_type=?,
                enabled=?,
                enable_reverse=?,
                magic_reverse=?,
                execution_type_rev=?,
                partial_tp_config=?,
                updated_at=?
            WHERE telegram_id=?
            """,
            (
                channel_name,
                magic,
                risk_pct,
                execution_type,
                enabled,
                enable_reverse,
                magic_reverse,
                execution_type_rev,
                partial_tp_config,
                now_ts,
                telegram_id,
            ),
        )
        cfg_id = row["id"]
    else:
        cur.execute(
            """
            INSERT INTO channel_config (
                channel_name, telegram_id, magic, risk_pct,
                execution_type, enabled, enable_reverse,
                magic_reverse, execution_type_rev,
                created_at, updated_at, partial_tp_config
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                channel_name,
                telegram_id,
                magic,
                risk_pct,
                execution_type,
                enabled,
                enable_reverse,
                magic_reverse,
                execution_type_rev,
                now_ts,
                now_ts,
                partial_tp_config,
            ),
        )
        cfg_id = cur.lastrowid

    conn.commit()
    conn.close()
    return cfg_id


def disable_channel_config(telegram_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE channel_config SET enabled=0, updated_at=? WHERE telegram_id=?",
        (int(time.time()), telegram_id),
    )
    conn.commit()
    conn.close()


def get_brokers_for_channel(channel_id: int) -> List[Dict]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM channel_brokers WHERE channel_id=? AND enabled=1",
        (channel_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]
