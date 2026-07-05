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

CREATE TABLE IF NOT EXISTS forecast_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id TEXT NOT NULL,      -- ticker
    ts TEXT NOT NULL,
    harness_version TEXT NOT NULL,
    p_raw REAL,                     -- supervisor output, pre-calibration
    p_calibrated REAL,              -- what trade.py consumes (via forecasts.p_model)
    calibration TEXT,               -- json, e.g. {"method":"extremize","alpha":1.3}
    confidence TEXT,
    rationale TEXT,
    crux TEXT,
    question_type TEXT,             -- from the analyst stage
    n_researchers INTEGER,
    researcher_spread REAL,         -- max - min of researcher probabilities
    supervisor_fallback INTEGER DEFAULT 0,
    status TEXT NOT NULL,           -- running / ok / failed
    duration_s REAL,
    as_of TEXT                      -- null for live; set for pastcasts
);

CREATE TABLE IF NOT EXISTS agent_traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES forecast_runs(id),
    stage TEXT NOT NULL,            -- analyst / researcher / supervisor
    agent_index INTEGER,            -- 0..K-1 for researchers
    output_json TEXT,               -- full parsed stage output
    sources TEXT,                   -- json array of URLs
    error TEXT,
    duration_s REAL
);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(forecasts)")}
    if "run_id" not in cols:
        conn.execute(
            "ALTER TABLE forecasts ADD COLUMN run_id INTEGER REFERENCES forecast_runs(id)")
        conn.commit()


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn
