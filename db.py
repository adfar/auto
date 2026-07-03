"""SQLite storage for the Kalshi paper-trading pipeline."""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "kalshi_edge.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS markets (
    ticker TEXT PRIMARY KEY,
    event_ticker TEXT,
    series_ticker TEXT,
    category TEXT,
    title TEXT,
    yes_sub_title TEXT,
    rules_primary TEXT,
    market_type TEXT,
    status TEXT,
    close_time TEXT,
    yes_bid REAL,
    yes_ask REAL,
    last_price REAL,
    volume REAL,
    open_interest REAL,
    liquidity REAL,
    snapshot_ts TEXT
);

CREATE TABLE IF NOT EXISTS forecasts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    ts TEXT NOT NULL,
    p_model REAL NOT NULL,          -- model probability of YES
    p_market REAL NOT NULL,         -- mid price at forecast time
    yes_bid REAL,
    yes_ask REAL,
    confidence TEXT,                -- low / medium / high (model self-report)
    rationale TEXT,
    model TEXT
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    forecast_id INTEGER REFERENCES forecasts(id),
    ticker TEXT NOT NULL,
    ts TEXT NOT NULL,
    side TEXT NOT NULL,             -- 'yes' or 'no'
    entry_price REAL NOT NULL,      -- price paid per contract (the ask for that side)
    contracts INTEGER NOT NULL,
    fee REAL NOT NULL,              -- total estimated taker fee for the fill
    p_model REAL NOT NULL,
    edge REAL NOT NULL,             -- expected value per contract after fees
    status TEXT NOT NULL DEFAULT 'open',   -- open / settled / voided
    result TEXT,                    -- yes / no once settled
    pnl REAL                        -- realized P&L after fees
);

CREATE TABLE IF NOT EXISTS settlements (
    ticker TEXT PRIMARY KEY,
    result TEXT NOT NULL,           -- yes / no
    settled_ts TEXT NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn
