"""Core ledger logic: balances, forecasts, and the actual/projection reconciliation.

Money is always integer cents. Dates are ISO 8601 strings (YYYY-MM-DD) so they
sort and compare lexicographically.
"""
import hashlib
import sqlite3
from dataclasses import dataclass
from typing import Optional


def get_account_id(conn: sqlite3.Connection, name: str) -> int:
    row = conn.execute("SELECT id FROM accounts WHERE name = ?", (name,)).fetchone()
    if row is None:
        raise ValueError(f"Nessun conto chiamato {name!r}. Crealo con: python -m cashmon.seed --account {name!r} --balance ... --date ...")
    return row["id"]


def list_accounts(conn: sqlite3.Connection):
    return conn.execute("SELECT id, name FROM accounts ORDER BY id").fetchall()


def get_initial_balance_cents(conn: sqlite3.Connection, account_id: int) -> int:
    row = conn.execute("SELECT initial_balance_cents FROM accounts WHERE id = ?", (account_id,)).fetchone()
    return row["initial_balance_cents"] if row else 0


def get_initial_balance_date(conn: sqlite3.Connection, account_id: int) -> str:
    row = conn.execute("SELECT initial_balance_date FROM accounts WHERE id = ?", (account_id,)).fetchone()
    return row["initial_balance_date"] if row else "1970-01-01"


def balance_at(conn: sqlite3.Connection, date: str, account_id: int) -> int:
    """Real balance of one account at `date`: its initial balance plus every
    non-superseded actual transaction on or before that date that represents
    real money movement (counts_toward_balance=1 -- see the transactions
    table comment for why a row can be excluded, e.g. an itemized Nexi card
    purchase whose cash impact is recorded separately, as the lump monthly
    settlement; or a transfer between two of the user's own accounts, tagged
    "Trasferimento interno" on both sides, which nets to zero across
    accounts without needing to link the two rows together)."""
    initial = get_initial_balance_cents(conn, account_id)
    row = conn.execute(
        """
        SELECT COALESCE(SUM(amount_cents), 0) AS total
        FROM transactions
        WHERE date <= ? AND account_id = ? AND superseded_by_id IS NULL AND counts_toward_balance = 1
        """,
        (date, account_id),
    ).fetchone()
    return initial + row["total"]


def total_balance_at(conn: sqlite3.Connection, date: str) -> int:
    """Total patrimonio at `date`: the sum of every tracked account's balance."""
    return sum(balance_at(conn, date, row["id"]) for row in list_accounts(conn))


def get_last_actual_date(conn: sqlite3.Connection, account_id: int) -> str:
    row = conn.execute(
        """
        SELECT MAX(date) AS last_date FROM transactions
        WHERE account_id = ? AND superseded_by_id IS NULL AND counts_toward_balance = 1
        """,
        (account_id,),
    ).fetchone()
    return row["last_date"] or get_initial_balance_date(conn, account_id)


def forecast_at(conn: sqlite3.Connection, date: str, account_id: int) -> int:
    """Forecast balance of one account at `date`: its real balance as of its
    last actual transaction, plus every still-unmatched projection up to
    `date`. Projections aren't tagged to an account yet (they're unused in
    practice so far), so all of them apply here regardless of account.

    A projection stops contributing once it is matched to a real transaction
    (matched_transaction_id set) -- it is never deleted, so "overwriting" a
    projection with actual data is an aggregation effect, not a destructive
    write, and forecast-vs-actual stays comparable after the fact.
    """
    last_actual = get_last_actual_date(conn, account_id)
    base = balance_at(conn, last_actual, account_id)
    row = conn.execute(
        """
        SELECT COALESCE(SUM(amount_cents), 0) AS total
        FROM projections
        WHERE date <= ? AND matched_transaction_id IS NULL
        """,
        (date,),
    ).fetchone()
    return base + row["total"]


def normalize_description(description: str) -> str:
    normalized = description.strip().upper()
    normalized = " ".join(normalized.split())
    return normalized


def compute_import_hash(source: str, date: str, amount_cents: int, description: str) -> str:
    normalized = normalize_description(description)
    payload = f"{source}|{date}|{amount_cents}|{normalized}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class InsertResult:
    transaction_id: Optional[int]
    inserted: bool  # False means it was a duplicate (import_hash collision), no-op


def add_transaction(
    conn: sqlite3.Connection,
    date: str,
    amount_cents: int,
    description: str,
    source: str,
    account_id: int,
    category: Optional[str] = None,
    status: str = "confirmed",
    import_hash: Optional[str] = None,
    counts_toward_balance: int = 1,
    dedupe: bool = True,
) -> InsertResult:
    """Insert an actual transaction. If import_hash collides with an existing
    row, this is a no-op (idempotent re-import) rather than an error.

    Set dedupe=False for interactively-entered rows (Telegram), where two
    genuinely separate transactions can share date/amount/description (two
    identical coffees bought the same day) -- hashing those would silently
    drop the second one as a "duplicate". This inserts import_hash=NULL,
    which SQLite's UNIQUE constraint never treats as a collision. Statement/
    CSV imports must keep dedupe=True (the default): there, re-running the
    same import being a no-op is the whole point.
    """
    if dedupe:
        if import_hash is None:
            import_hash = compute_import_hash(source, date, amount_cents, description)
    else:
        import_hash = None
    try:
        cur = conn.execute(
            """
            INSERT INTO transactions (date, amount_cents, description, category, status, source, import_hash, counts_toward_balance, account_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (date, amount_cents, description, category, status, source, import_hash, counts_toward_balance, account_id),
        )
        conn.commit()
        return InsertResult(transaction_id=cur.lastrowid, inserted=True)
    except sqlite3.IntegrityError:
        row = conn.execute(
            "SELECT id FROM transactions WHERE import_hash = ?", (import_hash,)
        ).fetchone()
        return InsertResult(transaction_id=row["id"] if row else None, inserted=False)


def supersede_transaction(conn: sqlite3.Connection, provisional_id: int, authoritative_id: int) -> None:
    """Mark a provisional transaction (e.g. a Telegram notification) as replaced
    by an authoritative one (e.g. the matching statement row), without deleting
    either -- the provisional row is excluded from balance_at going forward."""
    conn.execute(
        "UPDATE transactions SET superseded_by_id = ? WHERE id = ?",
        (authoritative_id, provisional_id),
    )
    conn.commit()


def add_projection(
    conn: sqlite3.Connection,
    date: str,
    amount_cents: int,
    description: str,
    category: Optional[str] = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO projections (date, amount_cents, description, category)
        VALUES (?, ?, ?, ?)
        """,
        (date, amount_cents, description, category),
    )
    conn.commit()
    return cur.lastrowid


def match_projection(conn: sqlite3.Connection, projection_id: int, transaction_id: int) -> None:
    """Link a projection to the actual transaction that realized it. The
    projection row is kept (not deleted) so forecast-vs-actual stays comparable;
    it simply stops contributing to forecast_at."""
    conn.execute(
        "UPDATE projections SET matched_transaction_id = ? WHERE id = ?",
        (transaction_id, projection_id),
    )
    conn.commit()
