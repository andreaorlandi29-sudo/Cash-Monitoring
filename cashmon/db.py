"""SQLite connection helpers."""
import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"

# Must match cashmon.seed.DEFAULT_ACCOUNT_NAME -- this is the account a
# pre-multi-account database's single implicit account is migrated into.
LEGACY_ACCOUNT_NAME = "Findomestic Conto Corrente"


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Creates any missing tables, then runs one-time migrations for
    databases created before multi-account support existed. Safe to call on
    every startup: each step only acts if it hasn't already been applied."""
    conn.executescript(SCHEMA_PATH.read_text())
    conn.commit()
    _migrate_to_multi_account(conn)


def _migrate_to_multi_account(conn: sqlite3.Connection) -> None:
    columns = [row["name"] for row in conn.execute("PRAGMA table_info(transactions)")]
    if "account_id" not in columns:
        conn.execute("ALTER TABLE transactions ADD COLUMN account_id INTEGER REFERENCES accounts(id)")
        conn.commit()

    config_balance = conn.execute(
        "SELECT value FROM config WHERE key = 'initial_balance_cents'"
    ).fetchone()
    if config_balance is None:
        return  # fresh database seeded directly via the accounts table; nothing to migrate

    legacy_account = conn.execute(
        "SELECT id FROM accounts WHERE name = ?", (LEGACY_ACCOUNT_NAME,)
    ).fetchone()
    if legacy_account is None:
        config_date = conn.execute(
            "SELECT value FROM config WHERE key = 'initial_balance_date'"
        ).fetchone()
        cur = conn.execute(
            "INSERT INTO accounts (name, initial_balance_cents, initial_balance_date) VALUES (?, ?, ?)",
            (LEGACY_ACCOUNT_NAME, int(config_balance["value"]), config_date["value"] if config_date else "1970-01-01"),
        )
        legacy_account_id = cur.lastrowid
        conn.commit()
    else:
        legacy_account_id = legacy_account["id"]

    conn.execute(
        "UPDATE transactions SET account_id = ? WHERE account_id IS NULL",
        (legacy_account_id,),
    )
    conn.commit()
