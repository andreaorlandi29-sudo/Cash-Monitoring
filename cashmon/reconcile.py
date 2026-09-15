"""Statement reconciliation: after importing a bank statement, links each
manually-entered ("telegram") transaction to the authoritative statement row
for the same real-world movement, so the same money never counts twice once
its statement line lands (see ledger.supersede_transaction and
add_transaction's dedupe=False, which is why manual entries are
"provisional" until this runs). Also checks the statement's own printed
closing balance against what cashmon computes, since a parser miss (a
statement layout change) degrades silently to "fewer rows imported", not an
error -- a plain row-count can't catch that, but a wrong balance can.
"""
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import List, Optional

from cashmon.categorizer import categorize
from cashmon.ledger import add_transaction, balance_at, supersede_transaction

MATCH_WINDOW_DAYS = 5


def _shift_date(iso_date: str, days: int) -> str:
    return (date.fromisoformat(iso_date) + timedelta(days=days)).isoformat()


@dataclass
class ReconcileResult:
    account_name: str
    inserted: int = 0
    duplicates: int = 0
    matched: int = 0
    closing_balance_cents: Optional[int] = None
    computed_balance_cents: Optional[int] = None
    unmatched_pending: List[sqlite3.Row] = field(default_factory=list)


def match_pending_in_range(conn: sqlite3.Connection, account_id: int, window_start: str, window_end: str) -> int:
    """Supersedes every still-pending manual entry ('telegram' source, counts
    toward balance, not yet superseded) dated in [window_start, window_end]
    on account_id that has an exact-amount authoritative match (any source
    other than 'telegram'/'satispay') within MATCH_WINDOW_DAYS of it.
    Matching is independent of which import inserted the authoritative row,
    so a manual entry logged after its statement already landed, or
    rechecked on a later run, still gets picked up. Returns how many were
    matched."""
    pending = conn.execute(
        """
        SELECT id, date, amount_cents FROM transactions
        WHERE account_id = ? AND source = 'telegram' AND counts_toward_balance = 1
          AND superseded_by_id IS NULL AND date BETWEEN ? AND ?
        ORDER BY date
        """,
        (account_id, window_start, window_end),
    ).fetchall()

    matched = 0
    used_authoritative_ids = set()
    for pending_row in pending:
        candidates = conn.execute(
            """
            SELECT id, date FROM transactions
            WHERE account_id = ? AND amount_cents = ? AND counts_toward_balance = 1
              AND source NOT IN ('telegram', 'satispay') AND superseded_by_id IS NULL
              AND date BETWEEN ? AND ?
            """,
            (
                account_id,
                pending_row["amount_cents"],
                _shift_date(pending_row["date"], -MATCH_WINDOW_DAYS),
                _shift_date(pending_row["date"], MATCH_WINDOW_DAYS),
            ),
        ).fetchall()

        best, best_distance = None, None
        for candidate in candidates:
            if candidate["id"] in used_authoritative_ids:
                continue
            distance = abs((date.fromisoformat(candidate["date"]) - date.fromisoformat(pending_row["date"])).days)
            if best_distance is None or distance < best_distance:
                best, best_distance = candidate, distance

        if best is not None:
            supersede_transaction(conn, pending_row["id"], best["id"])
            used_authoritative_ids.add(best["id"])
            matched += 1
    return matched


def list_unmatched_pending(conn: sqlite3.Connection, account_id: int, up_to_date: str):
    """Manual entries still uncorroborated by any statement, dated on or
    before `up_to_date` -- informational only, no writes. The upper bound
    matters: a very recent entry may simply not have posted yet, so callers
    should pass a date with some slack behind the statement's actual
    coverage (see reconcile_statement) rather than "today"."""
    return conn.execute(
        """
        SELECT id, date, amount_cents, description FROM transactions
        WHERE account_id = ? AND source = 'telegram' AND counts_toward_balance = 1
          AND superseded_by_id IS NULL AND date <= ?
        ORDER BY date
        """,
        (account_id, up_to_date),
    ).fetchall()


def reconcile_statement(
    conn: sqlite3.Connection,
    account_id: int,
    account_name: str,
    source: str,
    transactions: list,
    closing_balance_cents: Optional[int],
) -> ReconcileResult:
    """Imports `transactions` (as add_transaction already does for a plain
    import -- idempotent on re-upload), then matches and reports. Matching
    writes (supersede) are scoped to the statement's own date range, padded
    by MATCH_WINDOW_DAYS on both sides -- never wider -- so reconciling a new
    statement can't reach back and silently move balances that were already
    correct from a prior period."""
    result = ReconcileResult(account_name=account_name, closing_balance_cents=closing_balance_cents)

    for tx in transactions:
        category = categorize(conn, tx["description"])
        insert = add_transaction(
            conn,
            date=tx["date"],
            amount_cents=tx["amount_cents"],
            description=tx["description"],
            source=source,
            account_id=account_id,
            category=category,
            status="confirmed",
        )
        if insert.inserted:
            result.inserted += 1
        else:
            result.duplicates += 1

    if not transactions:
        return result

    start = min(tx["date"] for tx in transactions)
    end = max(tx["date"] for tx in transactions)
    window_start = _shift_date(start, -MATCH_WINDOW_DAYS)
    window_end = _shift_date(end, MATCH_WINDOW_DAYS)

    result.matched = match_pending_in_range(conn, account_id, window_start, window_end)
    result.unmatched_pending = list_unmatched_pending(conn, account_id, end)

    if closing_balance_cents is not None:
        result.computed_balance_cents = balance_at(conn, end, account_id)

    return result
