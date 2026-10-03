from cashmon.categorizer import seed_rules
from cashmon.ledger import add_transaction
from cashmon.recategorize import recategorize_uncategorized


def test_recategorize_fills_in_rows_that_now_match_a_rule(seeded_conn, account_id):
    # Simulates a row imported BEFORE the "BRANZI" rule existed: it landed
    # with no category, same as on a real, already-imported database.
    add_transaction(
        seeded_conn, "2026-08-24", -800, "Azienda agricola il se CARONA", source="findomestic_pdf", account_id=account_id
    )
    add_transaction(
        seeded_conn, "2026-08-21", -995, "Market alimentari BRANZI", source="findomestic_pdf", account_id=account_id
    )
    seed_rules(seeded_conn)  # adds the BRANZI -> Branzi rule (among others)

    updated = recategorize_uncategorized(seeded_conn)

    rows = {
        r["description"]: r["category"]
        for r in seeded_conn.execute("SELECT description, category FROM transactions")
    }
    assert rows["Market alimentari BRANZI"] == "Branzi"
    assert rows["Azienda agricola il se CARONA"] is None  # no matching rule -- correctly left alone
    assert updated == 1


def test_recategorize_never_touches_an_already_categorized_row(seeded_conn, account_id):
    add_transaction(
        seeded_conn,
        "2026-08-21",
        -995,
        "Market alimentari BRANZI",
        source="findomestic_pdf",
        account_id=account_id,
        category="Alimentari",  # user already categorized it manually, differently
    )
    seed_rules(seeded_conn)

    updated = recategorize_uncategorized(seeded_conn)

    category = seeded_conn.execute("SELECT category FROM transactions").fetchone()["category"]
    assert category == "Alimentari"  # untouched, not overwritten to Branzi
    assert updated == 0


def test_recategorize_is_a_noop_when_nothing_matches(seeded_conn, account_id):
    add_transaction(seeded_conn, "2026-08-24", -800, "Negozio sconosciuto XYZ", source="findomestic_pdf", account_id=account_id)
    seed_rules(seeded_conn)
    assert recategorize_uncategorized(seeded_conn) == 0
