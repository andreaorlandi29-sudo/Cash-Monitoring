from cashmon.ledger import add_transaction, balance_at
from cashmon.reconcile import list_unmatched_pending, match_pending_in_range, reconcile_statement


def _statement_row(date, amount_cents, description="BAR CENTRALE SRL"):
    return {"date": date, "amount_cents": amount_cents, "description": description}


def test_reconcile_matches_manual_entry_avoiding_double_count(seeded_conn, account_id):
    manual = add_transaction(
        seeded_conn, "2026-01-05", -350, "Bar", source="telegram", account_id=account_id, dedupe=False
    )
    result = reconcile_statement(
        seeded_conn, account_id, "Test Account", "findomestic_pdf",
        [_statement_row("2026-01-06", -350)], closing_balance_cents=None,
    )
    assert result.inserted == 1
    assert result.matched == 1
    assert result.unmatched_pending == []
    # Not doubled: only the authoritative row counts, the manual one is superseded.
    assert balance_at(seeded_conn, "2026-01-06", account_id) == 1_000_000 - 350

    manual_row = seeded_conn.execute("SELECT superseded_by_id FROM transactions WHERE id = ?", (manual.transaction_id,)).fetchone()
    assert manual_row["superseded_by_id"] is not None


def test_reconcile_is_idempotent_on_reimport(seeded_conn, account_id):
    add_transaction(seeded_conn, "2026-01-05", -350, "Bar", source="telegram", account_id=account_id, dedupe=False)
    transactions = [_statement_row("2026-01-06", -350)]

    first = reconcile_statement(seeded_conn, account_id, "Test Account", "findomestic_pdf", transactions, None)
    second = reconcile_statement(seeded_conn, account_id, "Test Account", "findomestic_pdf", transactions, None)

    assert first.inserted == 1 and first.matched == 1
    assert second.inserted == 0 and second.duplicates == 1 and second.matched == 0
    assert second.unmatched_pending == []
    assert balance_at(seeded_conn, "2026-01-06", account_id) == 1_000_000 - 350


def test_reconcile_reports_unmatched_manual_entry(seeded_conn, account_id):
    add_transaction(seeded_conn, "2026-01-05", -999, "Bar (importo sbagliato)", source="telegram", account_id=account_id, dedupe=False)
    result = reconcile_statement(
        seeded_conn, account_id, "Test Account", "findomestic_pdf",
        [_statement_row("2026-01-06", -350)], closing_balance_cents=None,
    )
    assert result.matched == 0
    assert len(result.unmatched_pending) == 1
    assert result.unmatched_pending[0]["amount_cents"] == -999


def test_reconcile_verifies_closing_balance_when_clean(seeded_conn, account_id):
    add_transaction(seeded_conn, "2026-01-05", -350, "Bar", source="telegram", account_id=account_id, dedupe=False)
    result = reconcile_statement(
        seeded_conn, account_id, "Test Account", "findomestic_pdf",
        [_statement_row("2026-01-06", -350)], closing_balance_cents=1_000_000 - 350,
    )
    assert result.computed_balance_cents == result.closing_balance_cents


def test_reconcile_flags_closing_balance_mismatch(seeded_conn, account_id):
    # A stray manual entry with no statement counterpart throws the computed
    # balance off from the statement's own printed closing balance.
    add_transaction(seeded_conn, "2026-01-05", -999, "Bar sbagliato", source="telegram", account_id=account_id, dedupe=False)
    result = reconcile_statement(
        seeded_conn, account_id, "Test Account", "findomestic_pdf",
        [_statement_row("2026-01-06", -350)], closing_balance_cents=1_000_000 - 350,
    )
    assert result.computed_balance_cents != result.closing_balance_cents
    assert result.computed_balance_cents - result.closing_balance_cents == -999


def test_matching_does_not_reach_outside_the_window(seeded_conn, account_id):
    # A manual entry from months earlier must NOT be superseded just because
    # a same-amount statement row shows up in an unrelated later import.
    old_manual = add_transaction(
        seeded_conn, "2026-01-05", -350, "Bar vecchio", source="telegram", account_id=account_id, dedupe=False
    )
    reconcile_statement(
        seeded_conn, account_id, "Test Account", "findomestic_pdf",
        [_statement_row("2026-06-06", -350)], closing_balance_cents=None,
    )
    row = seeded_conn.execute(
        "SELECT superseded_by_id FROM transactions WHERE id = ?", (old_manual.transaction_id,)
    ).fetchone()
    assert row["superseded_by_id"] is None


def test_list_unmatched_pending_excludes_satispay_and_superseded(seeded_conn, account_id):
    add_transaction(seeded_conn, "2026-01-05", -1200, "Satispay bar", source="satispay", account_id=account_id, counts_toward_balance=0, dedupe=False)
    add_transaction(seeded_conn, "2026-01-05", -350, "Bar", source="telegram", account_id=account_id, dedupe=False)
    rows = list_unmatched_pending(seeded_conn, account_id, "2026-01-31")
    assert len(rows) == 1
    assert rows[0]["amount_cents"] == -350


def test_match_pending_in_range_is_reusable_without_a_new_import(seeded_conn, account_id):
    # Simulates /riconcilia: a manual entry logged AFTER its statement row
    # already landed still gets matched on a later, standalone check.
    authoritative = add_transaction(
        seeded_conn, "2026-01-06", -350, "BAR CENTRALE SRL", source="findomestic_pdf", account_id=account_id
    )
    add_transaction(seeded_conn, "2026-01-05", -350, "Bar", source="telegram", account_id=account_id, dedupe=False)

    matched = match_pending_in_range(seeded_conn, account_id, "2026-01-01", "2026-01-31")
    assert matched == 1
    assert balance_at(seeded_conn, "2026-01-06", account_id) == 1_000_000 - 350
