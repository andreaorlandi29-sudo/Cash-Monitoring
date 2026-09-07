"""CSV bank-statement importer, driven by a declarative per-bank profile so a
new bank is a JSON config entry, not new code. See profiles/generic.json for
the shape of a profile.

Two amount conventions are supported via `amount_mode`:
  - "single": one signed amount column (negative = uscita, positive = entrata)
  - "debit_credit": separate debit/credit columns (debit is a positive number
    that gets negated)

Every imported row gets an import_hash, so re-importing the same statement
(or an overlapping date range from an updated export) is a no-op instead of
double-counting.
"""
import argparse
import csv
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from cashmon import config as app_config
from cashmon import db
from cashmon.categorizer import categorize
from cashmon.ledger import add_transaction

PROFILES_DIR = Path(__file__).resolve().parent / "profiles"


def load_profile(name: str) -> dict:
    path = PROFILES_DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"No import profile named '{name}' in {PROFILES_DIR}. "
            f"Available: {[p.stem for p in PROFILES_DIR.glob('*.json')]}"
        )
    return json.loads(path.read_text())


def parse_amount_to_cents(raw: str, decimal_sep: str, thousands_sep: str) -> int:
    value = raw.strip()
    if thousands_sep:
        value = value.replace(thousands_sep, "")
    if decimal_sep != ".":
        value = value.replace(decimal_sep, ".")
    return round(float(value) * 100)


def import_csv(conn: sqlite3.Connection, csv_path: str, profile: dict) -> dict:
    """Returns a summary dict: {"inserted": N, "duplicates": N, "categorized": N, "needs_category": N}."""
    summary = {"inserted": 0, "duplicates": 0, "categorized": 0, "needs_category": 0}
    decimal_sep = profile.get("decimal_separator", ".")
    thousands_sep = profile.get("thousands_separator", "")
    date_format = profile["date_format"]

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=profile.get("delimiter", ","))
        for row in reader:
            date = datetime.strptime(row[profile["date_col"]].strip(), date_format).strftime("%Y-%m-%d")
            description = row[profile["description_col"]].strip()

            if profile.get("amount_mode", "single") == "debit_credit":
                debit_raw = row.get(profile["debit_col"], "").strip()
                credit_raw = row.get(profile["credit_col"], "").strip()
                if debit_raw:
                    amount_cents = -abs(parse_amount_to_cents(debit_raw, decimal_sep, thousands_sep))
                else:
                    amount_cents = abs(parse_amount_to_cents(credit_raw, decimal_sep, thousands_sep))
            else:
                amount_cents = parse_amount_to_cents(row[profile["amount_col"]], decimal_sep, thousands_sep)

            category = categorize(conn, description)
            result = add_transaction(
                conn,
                date=date,
                amount_cents=amount_cents,
                description=description,
                source="csv_import",
                category=category,
                status="confirmed",
            )
            if result.inserted:
                summary["inserted"] += 1
                if category:
                    summary["categorized"] += 1
                else:
                    summary["needs_category"] += 1
            else:
                summary["duplicates"] += 1
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Import a bank statement CSV into cashmon.")
    parser.add_argument("csv_path")
    parser.add_argument("--profile", default="generic")
    parser.add_argument("--db", default=app_config.DB_PATH)
    args = parser.parse_args()

    conn = db.connect(args.db)
    profile = load_profile(args.profile)
    summary = import_csv(conn, args.csv_path, profile)
    print(
        f"Importate {summary['inserted']} righe nuove "
        f"({summary['categorized']} categorizzate, {summary['needs_category']} da categorizzare), "
        f"{summary['duplicates']} duplicati ignorati."
    )


if __name__ == "__main__":
    main()
