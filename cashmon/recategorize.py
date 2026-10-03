"""One-off maintenance: re-applies categorization_rules to already-imported
transactions that are still uncategorized.

Needed after adding a new rule for a pattern that already appears in
existing data (e.g. a merchant/place name) -- without this, only *future*
imports benefit from the new rule, and the existing backlog still has to be
categorized one by one via /categorizza even though the answer is now
obvious. Safe to re-run: it only ever fills in NULL categories, never
changes one that's already set.
"""
import argparse

from cashmon import config as app_config
from cashmon import db
from cashmon.categorizer import categorize


def recategorize_uncategorized(conn) -> int:
    rows = conn.execute("SELECT id, description FROM transactions WHERE category IS NULL").fetchall()
    updated = 0
    for row in rows:
        category = categorize(conn, row["description"])
        if category:
            conn.execute("UPDATE transactions SET category = ? WHERE id = ?", (category, row["id"]))
            updated += 1
    conn.commit()
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Riapplica le regole di categorizzazione alle spese già importate ma senza categoria."
    )
    parser.add_argument("--db", default=app_config.DB_PATH)
    args = parser.parse_args()

    conn = db.connect(args.db)
    db.init_schema(conn)
    updated = recategorize_uncategorized(conn)
    print(f"Ricategorizzate automaticamente {updated} righe.")


if __name__ == "__main__":
    main()
