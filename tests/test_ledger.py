import pytest

from cashmon.ledger import (
    add_projection,
    add_transaction,
    average_monthly_category_spend,
    balance_at,
    compute_import_hash,
    delete_projection,
    delete_transaction,
    forecast_at,
    forecast_breakdown,
    get_account_id,
    get_last_actual_date,
    list_projections,
    match_projection,
    supersede_transaction,
    total_balance_at,
    update_projection,
)


def test_balance_at_is_initial_balance_with_no_transactions(seeded_conn, account_id):
    assert balance_at(seeded_conn, "2026-01-01", account_id) == 1_000_000


def test_balance_at_sums_actuals_up_to_date(seeded_conn, account_id):
    add_transaction(seeded_conn, "2026-01-05", -5000, "Esselunga", source="csv_import", account_id=account_id)
    add_transaction(seeded_conn, "2026-01-10", 200000, "Stipendio", source="csv_import", account_id=account_id)
    assert balance_at(seeded_conn, "2026-01-05", account_id) == 1_000_000 - 5000
    assert balance_at(seeded_conn, "2026-01-09", account_id) == 1_000_000 - 5000
    assert balance_at(seeded_conn, "2026-01-10", account_id) == 1_000_000 - 5000 + 200000


def test_balance_at_excludes_superseded_transactions(seeded_conn, account_id):
    provisional = add_transaction(
        seeded_conn, "2026-01-05", -5000, "Notifica Esselunga", source="telegram", account_id=account_id, status="provisional"
    )
    authoritative = add_transaction(
        seeded_conn, "2026-01-05", -5000, "ESSELUNGA MILANO", source="csv_import", account_id=account_id
    )
    supersede_transaction(seeded_conn, provisional.transaction_id, authoritative.transaction_id)
    # Only the authoritative row should count -- not double-counted, not zero.
    assert balance_at(seeded_conn, "2026-01-05", account_id) == 1_000_000 - 5000


def test_add_transaction_is_idempotent_on_import_hash(seeded_conn, account_id):
    first = add_transaction(seeded_conn, "2026-01-05", -5000, "Esselunga", source="csv_import", account_id=account_id)
    second = add_transaction(seeded_conn, "2026-01-05", -5000, "Esselunga", source="csv_import", account_id=account_id)
    assert first.inserted is True
    assert second.inserted is False
    assert second.transaction_id == first.transaction_id
    assert balance_at(seeded_conn, "2026-01-05", account_id) == 1_000_000 - 5000


def test_forecast_at_includes_unmatched_projections(seeded_conn, account_id):
    add_projection(seeded_conn, "2026-02-01", -30000, "Affitto previsto")
    forecast = forecast_at(seeded_conn, "2026-02-01", account_id)
    assert forecast == 1_000_000 - 30000


def test_forecast_at_stops_counting_matched_projections(seeded_conn, account_id):
    projection_id = add_projection(seeded_conn, "2026-02-01", -30000, "Affitto previsto")
    result = add_transaction(seeded_conn, "2026-02-01", -30000, "Affitto Gennaio", source="csv_import", account_id=account_id)
    match_projection(seeded_conn, projection_id, result.transaction_id)

    # The projection no longer contributes (it's realized); the actual does, via balance_at.
    forecast = forecast_at(seeded_conn, "2026-02-01", account_id)
    assert forecast == 1_000_000 - 30000  # same total, but now backed by the actual, not double-counted


def test_forecast_at_does_not_double_count_matched_plus_unmatched(seeded_conn, account_id):
    matched_id = add_projection(seeded_conn, "2026-02-01", -30000, "Affitto previsto")
    add_projection(seeded_conn, "2026-03-01", -30000, "Affitto previsto marzo")
    result = add_transaction(seeded_conn, "2026-02-01", -30000, "Affitto Gennaio", source="csv_import", account_id=account_id)
    match_projection(seeded_conn, matched_id, result.transaction_id)

    forecast = forecast_at(seeded_conn, "2026-03-01", account_id)
    # base = balance_at(last actual = 2026-02-01) = 1_000_000 - 30000
    # plus unmatched projection (march) = -30000
    assert forecast == 1_000_000 - 30000 - 30000


def test_dedupe_false_allows_identical_repeated_interactive_entries(seeded_conn, account_id):
    # Two real, separate coffees bought the same day with the same typed
    # description must both be kept -- not silently collapsed into one.
    first = add_transaction(seeded_conn, "2026-01-05", -350, "Bar", source="telegram", account_id=account_id, dedupe=False)
    second = add_transaction(seeded_conn, "2026-01-05", -350, "Bar", source="telegram", account_id=account_id, dedupe=False)
    assert first.inserted is True
    assert second.inserted is True
    assert first.transaction_id != second.transaction_id
    assert balance_at(seeded_conn, "2026-01-05", account_id) == 1_000_000 - 700


def test_balance_at_excludes_rows_flagged_as_not_counting(seeded_conn, account_id):
    # e.g. an itemized Nexi card purchase: real spend for category reporting,
    # but not yet a movement of money out of the checking account.
    add_transaction(
        seeded_conn, "2026-01-05", -5000, "Amazon.it", source="nexi_card", account_id=account_id, counts_toward_balance=0
    )
    assert balance_at(seeded_conn, "2026-01-05", account_id) == 1_000_000
    assert get_last_actual_date(seeded_conn, account_id) == "2026-01-01"  # falls back to initial date


def test_get_account_id_looks_up_by_name(seeded_conn, account_id):
    assert get_account_id(seeded_conn, "Test Account") == account_id


def test_get_account_id_raises_for_unknown_account(seeded_conn):
    with pytest.raises(ValueError):
        get_account_id(seeded_conn, "Conto Inesistente")


def test_total_balance_at_sums_every_account(seeded_conn, account_id):
    # A second account (e.g. a linked deposit account).
    seeded_conn.execute(
        "INSERT INTO accounts (name, initial_balance_cents, initial_balance_date) VALUES (?, ?, ?)",
        ("Test Deposito", 500_000, "2026-01-01"),
    )
    seeded_conn.commit()
    deposit_id = get_account_id(seeded_conn, "Test Deposito")

    add_transaction(seeded_conn, "2026-01-05", -2000, "Trasferimento resto", source="findomestic_pdf", account_id=account_id)
    add_transaction(seeded_conn, "2026-01-05", 2000, "Trasferimento resto", source="findomestic_pdf", account_id=deposit_id)

    # A transfer between the user's own accounts nets to zero across the total.
    assert total_balance_at(seeded_conn, "2026-01-05") == 1_000_000 + 500_000
    assert balance_at(seeded_conn, "2026-01-05", account_id) == 1_000_000 - 2000
    assert balance_at(seeded_conn, "2026-01-05", deposit_id) == 500_000 + 2000


def _add_monthly_utenze(conn, account_id, dates_and_amounts):
    for date, amount in dates_and_amounts:
        add_transaction(conn, date, amount, "Bolletta", source="findomestic_pdf", account_id=account_id, category="Utenze")


def test_average_monthly_category_spend_divides_by_months_with_data_not_lookback(seeded_conn, account_id):
    # Only 3 months of history exist; dividing by the 6-month lookback window
    # would understate the average by half.
    _add_monthly_utenze(seeded_conn, account_id, [("2026-01-15", -6000), ("2026-02-15", -6000), ("2026-03-15", -6000)])
    avg, months = average_monthly_category_spend(seeded_conn, account_id, "Utenze", "2026-03-15", months_lookback=6)
    assert months == 3
    assert avg == -6000


def test_average_monthly_category_spend_no_data_returns_zero(seeded_conn, account_id):
    avg, months = average_monthly_category_spend(seeded_conn, account_id, "Utenze", "2026-03-15")
    assert (avg, months) == (0, 0)


def test_forecast_breakdown_adds_average_for_every_gap_month(seeded_conn, account_id):
    _add_monthly_utenze(seeded_conn, account_id, [("2026-01-15", -6000), ("2026-02-15", -6000), ("2026-03-15", -6000)])
    # last actual date is 2026-03-15; target 2026-05-20 spans two full gap
    # months (April, May) with no explicit Utenze projection in either.
    result = forecast_breakdown(seeded_conn, "2026-05-20", "2026-05-20", account_id, "Utenze")
    assert result["category_avg_cents"] == -6000
    assert result["category_avg_basis_months"] == 3
    assert result["category_gap_months"] == 2
    assert result["category_estimate_cents"] == -12000
    assert result["projections_cents"] == 0
    assert result["balance_today_cents"] == 1_000_000 - 18000
    assert result["total_cents"] == 1_000_000 - 18000 - 12000


def test_forecast_breakdown_explicit_projection_replaces_estimate_for_its_month(seeded_conn, account_id):
    _add_monthly_utenze(seeded_conn, account_id, [("2026-01-15", -6000), ("2026-02-15", -6000), ("2026-03-15", -6000)])
    without_projection = forecast_breakdown(seeded_conn, "2026-05-20", "2026-05-20", account_id, "Utenze")

    # An explicit Utenze bill logged for April: April should now come from
    # this real number, not the average -- only May (still uncovered) gets
    # the average added.
    add_projection(seeded_conn, "2026-04-10", -5000, "Bolletta luce", category="Utenze")
    with_projection = forecast_breakdown(seeded_conn, "2026-05-20", "2026-05-20", account_id, "Utenze")

    assert with_projection["category_gap_months"] == 1  # only May left uncovered
    assert with_projection["projections_cents"] == -5000
    assert with_projection["category_estimate_cents"] == -6000
    # Total moves by (projection - average), not by the projection alone --
    # that's the double-count check: April's average contribution (-6000)
    # got replaced by the real -5000, a net change of +1000.
    assert with_projection["total_cents"] - without_projection["total_cents"] == -5000 - (-6000)


def test_forecast_breakdown_same_month_as_last_actual_has_no_gap(seeded_conn, account_id):
    _add_monthly_utenze(seeded_conn, account_id, [("2026-03-15", -6000)])
    result = forecast_breakdown(seeded_conn, "2026-03-20", "2026-03-20", account_id, "Utenze")
    assert result["category_gap_months"] == 0
    assert result["category_estimate_cents"] == 0


def test_list_projections_excludes_matched_by_default(seeded_conn, account_id):
    pending_id = add_projection(seeded_conn, "2026-02-01", -30000, "Affitto previsto")
    matched_id = add_projection(seeded_conn, "2026-03-01", -5000, "Bolletta")
    result = add_transaction(seeded_conn, "2026-03-01", -5000, "Bolletta reale", source="findomestic_pdf", account_id=account_id)
    match_projection(seeded_conn, matched_id, result.transaction_id)

    rows = list_projections(seeded_conn)
    ids = [r["id"] for r in rows]
    assert pending_id in ids
    assert matched_id not in ids


def test_delete_projection_removes_an_unmatched_one(seeded_conn):
    projection_id = add_projection(seeded_conn, "2026-02-01", -30000, "Affitto previsto")
    assert delete_projection(seeded_conn, projection_id) is True
    assert list_projections(seeded_conn) == []


def test_delete_projection_refuses_unknown_id(seeded_conn):
    assert delete_projection(seeded_conn, 999) is False


def test_delete_projection_refuses_a_matched_projection(seeded_conn, account_id):
    projection_id = add_projection(seeded_conn, "2026-02-01", -30000, "Affitto previsto")
    result = add_transaction(seeded_conn, "2026-02-01", -30000, "Affitto reale", source="findomestic_pdf", account_id=account_id)
    match_projection(seeded_conn, projection_id, result.transaction_id)

    assert delete_projection(seeded_conn, projection_id) is False
    # still there, untouched
    row = seeded_conn.execute("SELECT id FROM projections WHERE id = ?", (projection_id,)).fetchone()
    assert row is not None


def test_update_projection_replaces_fields(seeded_conn):
    projection_id = add_projection(seeded_conn, "2026-02-01", -30000, "Affitto previsto")
    assert update_projection(seeded_conn, projection_id, "2026-02-15", -32000, "Affitto rivisto", "Casa") is True

    rows = list_projections(seeded_conn)
    assert len(rows) == 1
    assert rows[0]["date"] == "2026-02-15"
    assert rows[0]["amount_cents"] == -32000
    assert rows[0]["description"] == "Affitto rivisto"
    assert rows[0]["category"] == "Casa"


def test_update_projection_refuses_a_matched_projection(seeded_conn, account_id):
    projection_id = add_projection(seeded_conn, "2026-02-01", -30000, "Affitto previsto")
    result = add_transaction(seeded_conn, "2026-02-01", -30000, "Affitto reale", source="findomestic_pdf", account_id=account_id)
    match_projection(seeded_conn, projection_id, result.transaction_id)

    assert update_projection(seeded_conn, projection_id, "2026-02-15", -32000, "Nuova descrizione") is False


def test_compute_import_hash_is_stable_and_normalizes_description():
    h1 = compute_import_hash("csv_import", "2026-01-05", -5000, "esselunga  milano")
    h2 = compute_import_hash("csv_import", "2026-01-05", -5000, "ESSELUNGA MILANO")
    assert h1 == h2


def test_delete_transaction_removes_a_manual_entry(seeded_conn, account_id):
    result = add_transaction(seeded_conn, "2026-01-05", -350, "Bar", source="telegram", account_id=account_id, dedupe=False)
    assert delete_transaction(seeded_conn, result.transaction_id) is True
    row = seeded_conn.execute("SELECT id FROM transactions WHERE id = ?", (result.transaction_id,)).fetchone()
    assert row is None


def test_delete_transaction_removes_its_pending_question(seeded_conn, account_id):
    result = add_transaction(seeded_conn, "2026-01-05", -350, "Bar", source="telegram", account_id=account_id, dedupe=False)
    seeded_conn.execute(
        "INSERT INTO pending_questions (transaction_id, chat_id, message_id) VALUES (?, ?, ?)",
        (result.transaction_id, 1, 1),
    )
    seeded_conn.commit()
    assert delete_transaction(seeded_conn, result.transaction_id) is True
    row = seeded_conn.execute(
        "SELECT id FROM pending_questions WHERE transaction_id = ?", (result.transaction_id,)
    ).fetchone()
    assert row is None


def test_delete_transaction_refuses_a_nonexistent_id(seeded_conn):
    assert delete_transaction(seeded_conn, 999) is False


def test_delete_transaction_refuses_an_authoritative_import(seeded_conn, account_id):
    result = add_transaction(
        seeded_conn, "2026-01-05", -5000, "ESSELUNGA MILANO", source="findomestic_pdf", account_id=account_id
    )
    assert delete_transaction(seeded_conn, result.transaction_id) is False
    row = seeded_conn.execute("SELECT id FROM transactions WHERE id = ?", (result.transaction_id,)).fetchone()
    assert row is not None


def test_delete_transaction_refuses_a_row_matched_to_a_projection(seeded_conn, account_id):
    projection_id = add_projection(seeded_conn, "2026-02-01", -30000, "Affitto previsto")
    result = add_transaction(seeded_conn, "2026-02-01", -30000, "Affitto reale", source="telegram", account_id=account_id, dedupe=False)
    match_projection(seeded_conn, projection_id, result.transaction_id)

    assert delete_transaction(seeded_conn, result.transaction_id) is False
    row = seeded_conn.execute("SELECT id FROM transactions WHERE id = ?", (result.transaction_id,)).fetchone()
    assert row is not None
