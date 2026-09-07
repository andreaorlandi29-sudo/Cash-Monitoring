"""Nexi credit-card statement PDF importer.

Card purchases are NOT a separate movement of money out of the linked
checking account: Nexi settles the whole statement in one lump SDD debit,
roughly two months after the purchases were made (the Findomestic importer
picks that lump debit up as a normal transaction, categorized "Carta di
Credito" by the seed rules). So these individual line items are recorded
with counts_toward_balance=0: they carry the real category and purchase
date for spending reports, but don't move balance_at -- recording both
would double-count the same money.
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
    looks_like_amount,
    parse_italian_amount_to_cents,
)
from cashmon.ledger import add_transaction

DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{2}$")
TABLE_HEADER_MARKER = "DETTAGLIODEISUOIMOVIMENTI"
STOP_MARKER = "TOTALESPESE"
COMMISSION_PREFIX = "COMMISSIONE"


def _to_iso_date(raw: str) -> str:
    return datetime.strptime(raw, "%d/%m/%y").strftime("%Y-%m-%d")


def _is_anchor(line: dict) -> bool:
    words = line["words"]
    return bool(words) and bool(DATE_RE.match(words[0]["text"]))


def _build_anchor(line: dict) -> dict:
    words = line["words"]
    date_iso = _to_iso_date(words[0]["text"])
    inline_desc = []
    euro_amount = None
    for w in words[1:]:
        text = w["text"]
        if looks_like_amount(text):
            if euro_amount is None and MONEY_RE.match(text):
                euro_amount = text  # leftmost plain amount = Importo in Euro
        else:
            inline_desc.append(text)
    return {"date": date_iso, "inline_desc": inline_desc, "euro_amount": euro_amount}


def parse_pdf(pdf_path: str) -> list:
    """Returns a list of {date, description, amount_cents} dicts."""
    transactions = []
    stop = False
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            if stop:
                break
            lines = extract_lines(page)
            header_idx = next(
                (i for i, l in enumerate(lines) if l["text"].replace(" ", "").upper().startswith(TABLE_HEADER_MARKER)),
                None,
            )
            if header_idx is None:
                continue
            # Skip the section-title line itself and the column-header line
            # right after it ("Data Descrizione Importo in Euro ...").
            table_lines = lines[header_idx + 2 :]
            truncated = cut_before_marker(table_lines, STOP_MARKER)
            if len(truncated) != len(table_lines):
                stop = True
            table_lines = [
                l for l in truncated if not l["text"].replace(" ", "").upper().startswith(COMMISSION_PREFIX)
            ]
            rows = group_transaction_rows(table_lines, _is_anchor, _build_anchor)
            for row in rows:
                if not row["euro_amount"]:
                    continue
                amount_cents = -parse_italian_amount_to_cents(row["euro_amount"])
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
            source="nexi_card",
            category=category,
            status="confirmed",
            counts_toward_balance=0,
        )
        if result.inserted:
            summary["inserted"] += 1
            summary["categorized" if category else "needs_category"] += 1
        else:
            summary["duplicates"] += 1
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Importa un estratto conto Nexi (PDF).")
    parser.add_argument("pdf_path")
    parser.add_argument("--db", default=app_config.DB_PATH)
    args = parser.parse_args()

    conn = db.connect(args.db)
    summary = import_pdf(conn, args.pdf_path)
    print(
        f"Importate {summary['inserted']} spese carta "
        f"({summary['categorized']} categorizzate, {summary['needs_category']} da categorizzare), "
        f"{summary['duplicates']} duplicati ignorati. "
        f"Non influenzano il saldo: verranno addebitate in blocco fra circa 2 mesi "
        f"(importa l'estratto conto Findomestic di quel mese per registrare l'addebito reale)."
    )


if __name__ == "__main__":
    main()
