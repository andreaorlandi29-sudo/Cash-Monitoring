"""Findomestic (BNP Paribas) current-account statement PDF importer.

Layout: a borderless table with columns Data Contabile | Data Valuta |
Causale ABI | Descrizione dell'operazione | Uscite | Entrate. The column
x-coordinates below were measured on a real statement; if a future statement
shifts them, rows with dates/amounts outside these ranges are silently
skipped rather than mis-imported -- reconcile the import summary against the
statement's own "Entrate complessive" / "Uscite complessive" totals to catch
that.
"""
import argparse
import re
import sqlite3
from datetime import datetime

import pdfplumber

from cashmon import config as app_config
from cashmon import db
from cashmon.categorizer import categorize
from cashmon.importers.pdf_common import (
    MONEY_RE,
    clean_description,
    cut_before_marker,
    extract_lines,
    group_transaction_rows,
    parse_italian_amount_to_cents,
)
from cashmon.ledger import add_transaction

DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")

COL_DATA_CONTABILE_MAX_X = 85
COL_DESCRIZIONE = (178, 458)
COL_USCITE = (400, 500)
COL_ENTRATE = (500, 600)


def _is_anchor(line: dict) -> bool:
    words = line["words"]
    return bool(words) and words[0]["x0"] < COL_DATA_CONTABILE_MAX_X and bool(DATE_RE.match(words[0]["text"]))


def _build_anchor(line: dict) -> dict:
    words = line["words"]
    date_iso = datetime.strptime(words[0]["text"], "%d/%m/%Y").strftime("%Y-%m-%d")

    inline_desc, uscite, entrate = [], None, None
    for w in words[1:]:
        x0, text = w["x0"], w["text"]
        # Classify by COLUMN first: a money-shaped token that lands in the
        # description column (e.g. the "0,00" in "di cui 0,00 per
        # Commiss/Spese") is descriptive text, not an amount -- only a
        # money-shaped token actually inside the uscite/entrate column is a
        # real amount.
        if COL_USCITE[0] <= x0 < COL_USCITE[1] and MONEY_RE.match(text):
            uscite = text
        elif COL_ENTRATE[0] <= x0 < COL_ENTRATE[1] and MONEY_RE.match(text):
            entrate = text
        elif COL_DESCRIZIONE[0] <= x0 < COL_DESCRIZIONE[1]:
            inline_desc.append(text)

    return {"date": date_iso, "inline_desc": inline_desc, "uscite": uscite, "entrate": entrate}


def parse_pdf(pdf_path: str) -> list:
    """Returns a list of {date, description, amount_cents} dicts."""
    transactions = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            lines = extract_lines(page)
            header_idx = next(
                (
                    i
                    for i, l in enumerate(lines)
                    if "DESCRIZIONE" in l["text"].upper() and "OPERAZIONE" in l["text"].upper()
                ),
                None,
            )
            if header_idx is None:
                continue
            # Skip the header line itself and its "Contabile Valuta ABI"
            # continuation fragment right below it.
            table_lines = cut_before_marker(lines[header_idx + 2 :], "SALDO FINALE")
            rows = group_transaction_rows(table_lines, _is_anchor, _build_anchor)
            for row in rows:
                if row["uscite"] is None and row["entrate"] is None:
                    continue
                amount_cents = (
                    -parse_italian_amount_to_cents(row["uscite"])
                    if row["uscite"]
                    else parse_italian_amount_to_cents(row["entrate"])
                )
                description = clean_description(row["desc_words"]) or "(senza descrizione)"
                transactions.append({"date": row["date"], "description": description, "amount_cents": amount_cents})
    return transactions


def import_pdf(conn: sqlite3.Connection, pdf_path: str) -> dict:
    summary = {"inserted": 0, "duplicates": 0, "categorized": 0, "needs_category": 0}
    for tx in parse_pdf(pdf_path):
        category = categorize(conn, tx["description"])
        result = add_transaction(
            conn,
            date=tx["date"],
            amount_cents=tx["amount_cents"],
            description=tx["description"],
            source="findomestic_pdf",
            category=category,
            status="confirmed",
        )
        if result.inserted:
            summary["inserted"] += 1
            summary["categorized" if category else "needs_category"] += 1
        else:
            summary["duplicates"] += 1
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Importa un estratto conto Findomestic (PDF).")
    parser.add_argument("pdf_path")
    parser.add_argument("--db", default=app_config.DB_PATH)
    args = parser.parse_args()

    conn = db.connect(args.db)
    summary = import_pdf(conn, args.pdf_path)
    print(
        f"Importate {summary['inserted']} righe nuove "
        f"({summary['categorized']} categorizzate, {summary['needs_category']} da categorizzare), "
        f"{summary['duplicates']} duplicati ignorati."
    )


if __name__ == "__main__":
    main()
