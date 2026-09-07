from cashmon.ledger import (
    add_projection,
    add_transaction,
    balance_at,
    compute_import_hash,
    forecast_at,
    get_last_actual_date,
    match_projection,
    supersede_transaction,
)


def test_balance_at_is_initial_balance_with_no_transactions(seeded_conn):
    assert balance_at(seeded_conn, "2026-01-01") == 1_000_000


def test_balance_at_sums_actuals_up_to_date(seeded_conn):
    add_transaction(seeded_conn, "2026-01-05", -5000, "Esselunga", source="csv_import")
    add_transaction(seeded_conn, "2026-01-10", 200000, "Stipendio", source="csv_import")
    assert balance_at(seeded_conn, "2026-01-05") == 1_000_000 - 5000
    assert balance_at(seeded_conn, "2026-01-09") == 1_000_000 - 5000
    assert balance_at(seeded_conn, "2026-01-10") == 1_000_000 - 5000 + 200000


def test_balance_at_excludes_superseded_transactions(seeded_conn):
    provisional = add_transaction(seeded_conn, "2026-01-05", -5000, "Notifica Esselunga", source="telegram", status="provisional")
    authoritative = add_transaction(seeded_conn, "2026-01-05", -5000, "ESSELUNGA MILANO", source="csv_import")
    supersede_transaction(seeded_conn, provisional.transaction_id, authoritative.transaction_id)
    # Only the authoritative row should count -- not double-counted, not zero.
    assert balance_at(seeded_conn, "2026-01-05") == 1_000_000 - 5000


def test_add_transaction_is_idempotent_on_import_hash(seeded_conn):
    first = add_transaction(seeded_conn, "2026-01-05", -5000, "Esselunga", source="csv_import")
    second = add_transaction(seeded_conn, "2026-01-05", -5000, "Esselunga", source="csv_import")
    assert first.inserted is True
    assert second.inserted is False
    assert second.transaction_id == first.transaction_id
    assert balance_at(seeded_conn, "2026-01-05") == 1_000_000 - 5000


def test_forecast_at_includes_unmatched_projections(seeded_conn):
    add_projection(seeded_conn, "2026-02-01", -30000, "Affitto previsto")
    forecast = forecast_at(seeded_conn, "2026-02-01")
    assert forecast == 1_000_000 - 30000


def test_forecast_at_stops_counting_matched_projections(seeded_conn):
    projection_id = add_projection(seeded_conn, "2026-02-01", -30000, "Affitto previsto")
    result = add_transaction(seeded_conn, "2026-02-01", -30000, "Affitto Gennaio", source="csv_import")
    match_projection(seeded_conn, projection_id, result.transaction_id)

    # The projection no longer contributes (it's realized); the actual does, via balance_at.
    forecast = forecast_at(seeded_conn, "2026-02-01")
    assert forecast == 1_000_000 - 30000  # same total, but now backed by the actual, not double-counted


def test_forecast_at_does_not_double_count_matched_plus_unmatched(seeded_conn):
    matched_id = add_projection(seeded_conn, "2026-02-01", -30000, "Affitto previsto")
    add_projection(seeded_conn, "2026-03-01", -30000, "Affitto previsto marzo")
    result = add_transaction(seeded_conn, "2026-02-01", -30000, "Affitto Gennaio", source="csv_import")
    match_projection(seeded_conn, matched_id, result.transaction_id)

    forecast = forecast_at(seeded_conn, "2026-03-01")
    # base = balance_at(last actual = 2026-02-01) = 1_000_000 - 30000
    # plus unmatched projection (march) = -30000
    assert forecast == 1_000_000 - 30000 - 30000


def test_dedupe_false_allows_identical_repeated_interactive_entries(seeded_conn):
    # Two real, separate coffees bought the same day with the same typed
    # description must both be kept -- not silently collapsed into one.
    first = add_transaction(seeded_conn, "2026-01-05", -350, "Bar", source="telegram", dedupe=False)
    second = add_transaction(seeded_conn, "2026-01-05", -350, "Bar", source="telegram", dedupe=False)
    assert first.inserted is True
    assert second.inserted is True
    assert first.transaction_id != second.transaction_id
    assert balance_at(seeded_conn, "2026-01-05") == 1_000_000 - 700


def test_balance_at_excludes_rows_flagged_as_not_counting(seeded_conn):
    # e.g. an itemized Nexi card purchase: real spend for category reporting,
    # but not yet a movement of money out of the checking account.
    add_transaction(
        seeded_conn, "2026-01-05", -5000, "Amazon.it", source="nexi_card", counts_toward_balance=0
    )
    assert balance_at(seeded_conn, "2026-01-05") == 1_000_000
    assert get_last_actual_date(seeded_conn) == "2026-01-01"  # falls back to initial date


def test_compute_import_hash_is_stable_and_normalizes_description():
    h1 = compute_import_hash("csv_import", "2026-01-05", -5000, "esselunga  milano")
    h2 = compute_import_hash("csv_import", "2026-01-05", -5000, "ESSELUNGA MILANO")
    assert h1 == h2
