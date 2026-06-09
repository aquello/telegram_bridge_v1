import os
import sqlite3
import time
from typing import Any, Dict, List, Optional

DB_PATH_REAL = r"C:\Users\AdmVps\AppData\Roaming\MetaQuotes\Terminal\Common\Files\signals.db"
DB_PATH_BT = os.path.join(os.path.dirname(DB_PATH_REAL), "signals_bt.db")
DB_PATH = DB_PATH_REAL


def use_backtest_db():
    global DB_PATH
    DB_PATH = DB_PATH_BT


def use_real_db():
    global DB_PATH
    DB_PATH = DB_PATH_REAL


def get_connection() -> sqlite3.Connection:
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

CREATE TABLE IF NOT EXISTS signal_events (
    id INTEGER PRIMARY KEY,
    raw_message_id INTEGER,
    signal_id INTEGER,
    channel_name TEXT,
    telegram_id INTEGER,
    tg_message_id INTEGER,
    tg_reply_to_id INTEGER,
    symbol TEXT,
    event_type TEXT NOT NULL,
    event_value TEXT,
    event_price REAL,
    created_at INTEGER NOT NULL,
    status TEXT DEFAULT 'PENDING',
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

CREATE INDEX IF NOT EXISTS idx_raw_messages_channel_msg
ON raw_messages(channel_id, message_id);

CREATE INDEX IF NOT EXISTS idx_signals_channel_msg
ON signals(channel_name, tg_message_id);

CREATE INDEX IF NOT EXISTS idx_signal_events_status
ON signal_events(status, exported_mt4);

CREATE INDEX IF NOT EXISTS idx_signal_events_msg
ON signal_events(channel_name, tg_message_id, tg_reply_to_id);
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


def insert_raw_message(data: Dict[str, Any]) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO raw_messages (
            channel_id, channel_name, message_id, date, text, raw_json
        ) VALUES (
            :channel_id, :channel_name, :message_id, :date, :text, :raw_json
        )
        """,
        data,
    )
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return rid


def insert_signal(data: Dict[str, Any]) -> int:
    conn = get_connection()
    data = dict(data)

    for k in (
        "risk_pct", "volume_hint", "entry_min", "entry_max", "magic",
        "tp_label", "error_msg", "tp", "sl", "tp1", "tp2", "tp3",
        "tp4", "tp5", "tp6", "tp7", "tp8", "tp9", "tp10",
        "tg_reply_to_id"
    ):
        data.setdefault(k, None)

    data.setdefault("status", "PENDING")
    data.setdefault("tp_stage", 0)
    data.setdefault("action", None)
    data.setdefault("direction", None)
    data.setdefault("symbol", None)
    data.setdefault("execution_type", "MARKET")
    data.setdefault("created_at", int(time.time()))

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


def insert_signal_event(data: Dict[str, Any]) -> int:
    conn = get_connection()
    cur = conn.cursor()

    payload = {
        "raw_message_id": data.get("raw_message_id"),
        "signal_id": data.get("signal_id"),
        "channel_name": data.get("channel_name"),
        "telegram_id": data.get("telegram_id"),
        "tg_message_id": data.get("tg_message_id"),
        "tg_reply_to_id": data.get("tg_reply_to_id"),
        "symbol": data.get("symbol"),
        "event_type": data.get("event_type"),
        "event_value": data.get("event_value"),
        "event_price": data.get("event_price"),
        "created_at": data.get("created_at", int(time.time())),
        "status": data.get("status", "PENDING"),
        "exported_mt4": data.get("exported_mt4", 0),
    }

    cur.execute(
        """
        INSERT INTO signal_events (
            raw_message_id, signal_id, channel_name, telegram_id,
            tg_message_id, tg_reply_to_id, symbol, event_type,
            event_value, event_price, created_at, status, exported_mt4
        ) VALUES (
            :raw_message_id, :signal_id, :channel_name, :telegram_id,
            :tg_message_id, :tg_reply_to_id, :symbol, :event_type,
            :event_value, :event_price, :created_at, :status, :exported_mt4
        )
        """,
        payload,
    )

    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return rid


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
    cur.execute("SELECT * FROM channel_config WHERE enabled=1")
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


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
