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


def delete_transaction(conn: sqlite3.Connection, transaction_id: int) -> bool:
    """Deletes a manually-entered transaction (source 'telegram' or
    'satispay') -- the escape hatch for a typo/duplicate caught during
    statement reconciliation. Refuses (returns False, no-op) for anything
    that isn't a plain manual entry: an authoritative import (statement/CSV/
    Nexi) is bank-of-record and must not be editable from chat, and a row
    still referenced by a projection's matched_transaction_id is
    reconciliation history a delete would silently corrupt. Any
    pending_questions rows for it are cleaned up too -- they're pure
    message-routing scratch data with no value once the transaction is gone."""
    row = conn.execute("SELECT source FROM transactions WHERE id = ?", (transaction_id,)).fetchone()
    if row is None or row["source"] not in ("telegram", "satispay"):
        return False
    referenced = conn.execute(
        "SELECT 1 FROM projections WHERE matched_transaction_id = ?", (transaction_id,)
    ).fetchone()
    if referenced is not None:
        return False
    conn.execute("DELETE FROM pending_questions WHERE transaction_id = ?", (transaction_id,))
    conn.execute("DELETE FROM transactions WHERE id = ?", (transaction_id,))
    conn.commit()
    return True


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


def list_projections(conn: sqlite3.Connection, only_unmatched: bool = True):
    """Pending projections, oldest first. Matched ones (already reconciled
    against a real transaction) are excluded by default -- they're
    historical record, not a plan still waiting to happen."""
    if only_unmatched:
        return conn.execute(
            """
            SELECT id, date, amount_cents, description, category
            FROM projections WHERE matched_transaction_id IS NULL ORDER BY date ASC
            """
        ).fetchall()
    return conn.execute(
        "SELECT id, date, amount_cents, description, category, matched_transaction_id FROM projections ORDER BY date ASC"
    ).fetchall()


def get_projection(conn: sqlite3.Connection, projection_id: int):
    return conn.execute("SELECT * FROM projections WHERE id = ?", (projection_id,)).fetchone()


def delete_projection(conn: sqlite3.Connection, projection_id: int) -> bool:
    """Deletes an unmatched projection. Returns False (no-op) if it doesn't
    exist or has already been matched to a real transaction -- a matched
    projection is reconciliation history, not a pending plan, and isn't
    something a "delete" request should be able to remove."""
    row = get_projection(conn, projection_id)
    if row is None or row["matched_transaction_id"] is not None:
        return False
    conn.execute("DELETE FROM projections WHERE id = ?", (projection_id,))
    conn.commit()
    return True


def update_projection(
    conn: sqlite3.Connection,
    projection_id: int,
    date: str,
    amount_cents: int,
    description: str,
    category: Optional[str] = None,
) -> bool:
    """Replaces an unmatched projection's fields in place. Returns False
    (no-op), same reasoning as delete_projection, if it doesn't exist or is
    already matched."""
    row = get_projection(conn, projection_id)
    if row is None or row["matched_transaction_id"] is not None:
        return False
    conn.execute(
        "UPDATE projections SET date = ?, amount_cents = ?, description = ?, category = ? WHERE id = ?",
        (date, amount_cents, description, category, projection_id),
    )
    conn.commit()
    return True


def match_projection(conn: sqlite3.Connection, projection_id: int, transaction_id: int) -> None:
    """Link a projection to the actual transaction that realized it. The
    projection row is kept (not deleted) so forecast-vs-actual stays comparable;
    it simply stops contributing to forecast_at."""
    conn.execute(
        "UPDATE projections SET matched_transaction_id = ? WHERE id = ?",
        (transaction_id, projection_id),
    )
    conn.commit()


def _shift_year_month(year_month: str, delta_months: int) -> str:
    """'2026-08' shifted by -6 -> '2026-02'."""
    y, m = (int(p) for p in year_month.split("-"))
    total = y * 12 + (m - 1) + delta_months
    y2, m2 = divmod(total, 12)
    return f"{y2:04d}-{m2 + 1:02d}"


def _year_months_between(start_year_month: str, end_year_month: str):
    """'YYYY-MM' strings strictly after `start_year_month`, up to and
    including `end_year_month` (empty if end <= start)."""
    y1, m1 = (int(p) for p in start_year_month.split("-"))
    y2, m2 = (int(p) for p in end_year_month.split("-"))
    n1, n2 = y1 * 12 + (m1 - 1), y2 * 12 + (m2 - 1)
    return [f"{n // 12:04d}-{n % 12 + 1:02d}" for n in range(n1 + 1, n2 + 1)]


def average_monthly_category_spend(
    conn: sqlite3.Connection, account_id: int, category: str, as_of_date: str, months_lookback: int = 6
):
    """Average monthly amount (signed cents; negative for an expense
    category) for `category` on `account_id`, over the trailing
    `months_lookback` months ending at `as_of_date`.

    Divides by the number of DISTINCT months that actually have data, not by
    `months_lookback` -- with one month of history, dividing by 6 would
    understate the average sixfold. Returns (avg_cents, months_with_data) so
    the caller can flag a low-confidence estimate (few months of history).
    """
    cutoff_year_month = _shift_year_month(as_of_date[:7], -months_lookback)
    row = conn.execute(
        """
        SELECT COALESCE(SUM(amount_cents), 0) AS total,
               COUNT(DISTINCT substr(date, 1, 7)) AS months
        FROM transactions
        WHERE account_id = ? AND category = ? AND counts_toward_balance = 1
          AND superseded_by_id IS NULL AND substr(date, 1, 7) >= ?
        """,
        (account_id, category, cutoff_year_month),
    ).fetchone()
    months = row["months"] or 0
    if months == 0:
        return 0, 0
    return row["total"] // months, months


def forecast_breakdown(
    conn: sqlite3.Connection,
    today: str,
    target_date: str,
    account_id: int,
    category: str,
    months_lookback: int = 6,
) -> dict:
    """Projects the balance at `target_date` from three ingredients:

    1. Today's real balance.
    2. Every explicit unmatched projection dated on or before `target_date`
       (any category -- these are things the user already told the bot
       about, e.g. "previsione spesa ... Rata condominio").
    3. An automatic estimate for `category` (e.g. "Utenze"), based on its
       trailing average monthly spend, for every month between the last
       actual transaction and `target_date` that does NOT already have an
       explicit unmatched projection in that category.

    That last condition is what keeps this from double-counting: a month
    where the user already logged an expected Utenze bill contributes that
    bill's real amount (via ingredient 2) and nothing from the average (it's
    excluded from ingredient 3), rather than both.
    """
    balance_today_cents = balance_at(conn, today, account_id)

    projections_row = conn.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) AS total FROM projections WHERE date <= ? AND matched_transaction_id IS NULL",
        (target_date,),
    ).fetchone()
    projections_cents = projections_row["total"]

    last_actual = get_last_actual_date(conn, account_id)
    avg_cents, avg_basis_months = average_monthly_category_spend(conn, account_id, category, last_actual, months_lookback)

    all_months = _year_months_between(last_actual[:7], target_date[:7])
    covered_rows = conn.execute(
        """
        SELECT DISTINCT substr(date, 1, 7) AS ym FROM projections
        WHERE category = ? AND matched_transaction_id IS NULL AND date <= ?
        """,
        (category, target_date),
    ).fetchall()
    covered_months = {r["ym"] for r in covered_rows}
    gap_months = [m for m in all_months if m not in covered_months]
    category_estimate_cents = avg_cents * len(gap_months)

    return {
        "balance_today_cents": balance_today_cents,
        "projections_cents": projections_cents,
        "category": category,
        "category_avg_cents": avg_cents,
        "category_avg_basis_months": avg_basis_months,
        "category_gap_months": len(gap_months),
        "category_estimate_cents": category_estimate_cents,
        "total_cents": balance_today_cents + projections_cents + category_estimate_cents,
    }
