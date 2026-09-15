"""Verifies that a database created before multi-account support (a single
implicit account tracked via the `config` table, transactions with no
account_id column at all) upgrades in place -- same balance, same
categories -- when opened by current code. This is the migration path every
real database on a user's machine goes through the first time they run the
new code; losing or corrupting real, already-categorized data here would be
the worst failure mode this project has.
"""
import sqlite3

from cashmon import db
from cashmon.ledger import balance_at, get_account_id

# The exact shape of transactions/config before accounts/account_id existed.
OLD_SCHEMA = """
CREATE TABLE config (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    description TEXT NOT NULL,
    category TEXT,
    status TEXT NOT NULL DEFAULT 'confirmed',
    source TEXT NOT NULL,
    import_hash TEXT UNIQUE,
    superseded_by_id INTEGER REFERENCES transactions(id),
    counts_toward_balance INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _build_old_schema_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(OLD_SCHEMA)
    conn.execute("INSERT INTO config (key, value) VALUES ('initial_balance_cents', '400477')")
    conn.execute("INSERT INTO config (key, value) VALUES ('initial_balance_date', '2026-08-03')")
    conn.execute(
        "INSERT INTO transactions (date, amount_cents, description, category, source) VALUES (?, ?, ?, ?, ?)",
        ("2026-08-05", -4166, "Telecom", "Utenze", "findomestic_pdf"),
    )
    conn.execute(
        "INSERT INTO transactions (date, amount_cents, description, category, source) VALUES (?, ?, ?, ?, ?)",
        ("2026-08-10", 295853, "Stipendio", "Stipendio", "findomestic_pdf"),
    )
    conn.commit()
    return conn


def test_migration_preserves_balance_and_categories():
    conn = _build_old_schema_db()

    # This is exactly what db.connect() + db.init_schema() does on every real
    # app startup -- the migration must run automatically, not as a separate
    # manual step someone could forget.
    db.init_schema(conn)

    account_id = get_account_id(conn, db.LEGACY_ACCOUNT_NAME)
    assert balance_at(conn, "2026-08-10", account_id) == 400477 - 4166 + 295853

    categories = [r["category"] for r in conn.execute("SELECT category FROM transactions ORDER BY id")]
    assert categories == ["Utenze", "Stipendio"]


def test_migration_is_idempotent_on_repeated_startups():
    conn = _build_old_schema_db()
    db.init_schema(conn)
    db.init_schema(conn)  # second app startup against the now-migrated db

    account_id = get_account_id(conn, db.LEGACY_ACCOUNT_NAME)
    accounts_named_legacy = conn.execute(
        "SELECT COUNT(*) AS n FROM accounts WHERE name = ?", (db.LEGACY_ACCOUNT_NAME,)
    ).fetchone()["n"]
    assert accounts_named_legacy == 1  # no duplicate account row
    assert balance_at(conn, "2026-08-10", account_id) == 400477 - 4166 + 295853


def test_fresh_database_has_no_legacy_account():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.init_schema(conn)
    accounts = conn.execute("SELECT COUNT(*) AS n FROM accounts").fetchone()["n"]
    assert accounts == 0  # nothing to migrate -- config has no initial_balance_cents
