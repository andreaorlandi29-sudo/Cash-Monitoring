from cashmon.categorizer import seed_rules
from cashmon.importers.csv_importer import import_csv, load_profile
from cashmon.ledger import balance_at

CSV_CONTENT = """data,descrizione,importo
2026-01-05,ESSELUNGA MILANO,-45.30
2026-01-10,STIPENDIO GENNAIO,1500.00
"""


def write_csv(tmp_path, content=CSV_CONTENT):
    path = tmp_path / "estratto.csv"
    path.write_text(content, encoding="utf-8")
    return str(path)


def test_import_csv_inserts_and_categorizes(seeded_conn, tmp_path):
    seed_rules(seeded_conn)
    csv_path = write_csv(tmp_path)
    profile = load_profile("generic")

    summary = import_csv(seeded_conn, csv_path, profile)

    assert summary["inserted"] == 2
    assert summary["duplicates"] == 0
    assert summary["categorized"] == 2  # ESSELUNGA -> Alimentari, STIPENDIO GENNAIO -> Stipendio
    assert summary["needs_category"] == 0
    assert balance_at(seeded_conn, "2026-01-10") == 1_000_000 - 4530 + 150000


def test_import_csv_is_idempotent(seeded_conn, tmp_path):
    seed_rules(seeded_conn)
    csv_path = write_csv(tmp_path)
    profile = load_profile("generic")

    import_csv(seeded_conn, csv_path, profile)
    summary = import_csv(seeded_conn, csv_path, profile)

    assert summary["inserted"] == 0
    assert summary["duplicates"] == 2
    assert balance_at(seeded_conn, "2026-01-10") == 1_000_000 - 4530 + 150000
