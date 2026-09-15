import pytest

from cashmon.ledger import (
    add_projection,
    add_transaction,
    balance_at,
    compute_import_hash,
    forecast_at,
    get_account_id,
    get_last_actual_date,
    match_projection,
    supersede_transaction,
    total_balance_at,
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


def test_compute_import_hash_is_stable_and_normalizes_description():
    h1 = compute_import_hash("csv_import", "2026-01-05", -5000, "esselunga  milano")
    h2 = compute_import_hash("csv_import", "2026-01-05", -5000, "ESSELUNGA MILANO")
    assert h1 == h2
