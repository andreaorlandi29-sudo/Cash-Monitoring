"""Core ledger logic: balances, forecasts, and the actual/projection reconciliation.

Money is always integer cents. Dates are ISO 8601 strings (YYYY-MM-DD) so they
sort and compare lexicographically.
"""
import hashlib
import sqlite3
from dataclasses import dataclass
from typing import Optional


def get_initial_balance_cents(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM config WHERE key = 'initial_balance_cents'").fetchone()
    return int(row["value"]) if row else 0


def get_initial_balance_date(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT value FROM config WHERE key = 'initial_balance_date'").fetchone()
    return row["value"] if row else "1970-01-01"


def balance_at(conn: sqlite3.Connection, date: str) -> int:
    """Real balance at `date`: initial balance plus every non-superseded actual
    transaction on or before that date."""
    initial = get_initial_balance_cents(conn)
    row = conn.execute(
        """
        SELECT COALESCE(SUM(amount_cents), 0) AS total
        FROM transactions
        WHERE date <= ? AND superseded_by_id IS NULL
        """,
        (date,),
    ).fetchone()
    return initial + row["total"]


def get_last_actual_date(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT MAX(date) AS last_date FROM transactions WHERE superseded_by_id IS NULL"
    ).fetchone()
    return row["last_date"] or get_initial_balance_date(conn)


def forecast_at(conn: sqlite3.Connection, date: str) -> int:
    """Forecast balance at `date`: the real balance as of the last actual
    transaction, plus every still-unmatched projection up to `date`.

    A projection stops contributing once it is matched to a real transaction
    (matched_transaction_id set) -- it is never deleted, so "overwriting" a
    projection with actual data is an aggregation effect, not a destructive
    write, and forecast-vs-actual stays comparable after the fact.
    """
    last_actual = get_last_actual_date(conn)
    base = balance_at(conn, last_actual)
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
    category: Optional[str] = None,
    status: str = "confirmed",
    import_hash: Optional[str] = None,
) -> InsertResult:
    """Insert an actual transaction. If import_hash collides with an existing
    row, this is a no-op (idempotent re-import) rather than an error."""
    if import_hash is None:
        import_hash = compute_import_hash(source, date, amount_cents, description)
    try:
        cur = conn.execute(
            """
            INSERT INTO transactions (date, amount_cents, description, category, status, source, import_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (date, amount_cents, description, category, status, source, import_hash),
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
