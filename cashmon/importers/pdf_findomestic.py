"""Findomestic (BNP Paribas) statement PDF importer -- current account and
linked deposit account both use the same borderless-table template, with
slightly different columns, so one parser covers both:

  - Conto Corrente: Data Contabile | Data Valuta | Causale ABI |
    Descrizione dell'operazione | Uscite | Entrate
  - Conto Deposito: Data Contabile | Data Valuta | Descrizione delle
    operazioni | Uscite | Entrate (no Causale column, single-line header)

Which one a given PDF is gets auto-detected from its title line ("Estratto
Conto n°..." vs "Estratto Conto Deposito n°..."), which also picks the
matching account by name -- that account must already exist (seed it first
with `python -m cashmon.seed --account "Findomestic Conto Deposito" ...`).

The column x-coordinates below were measured on real statements; if a future
statement shifts them, rows with dates/amounts outside these ranges are
silently skipped rather than mis-imported -- reconcile the import summary
against the statement's own "Entrate complessive" / "Uscite complessive" (or
"SALDO INIZIALE" / "SALDO FINALE") totals to catch that.
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
from cashmon.ledger import add_transaction, get_account_id

DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
COL_DATA_CONTABILE_MAX_X = 85

LAYOUTS = {
    "corrente": {
        "account_name": "Findomestic Conto Corrente",
        # "Descrizione dell'operazione" is followed by a "Contabile Valuta
        # ABI" continuation fragment of the two-line stacked header -- skip
        # both before the real data rows start.
        "header_lines_to_skip": 2,
        "col_desc": (178, 458),
        "col_uscite": (400, 500),
        "col_entrate": (500, 600),
    },
    "deposito": {
        "account_name": "Findomestic Conto Deposito",
        # "Descrizione delle operazioni" is a single line here -- no
        # continuation fragment, no Causale column.
        "header_lines_to_skip": 1,
        "col_desc": (145, 459),
        "col_uscite": (440, 505),
        "col_entrate": (505, 600),
    },
}


def _detect_layout(first_page_lines: list) -> dict:
    title = " ".join(l["text"] for l in first_page_lines[:1]).upper()
    return LAYOUTS["deposito"] if "DEPOSITO" in title else LAYOUTS["corrente"]


def _is_anchor(line: dict) -> bool:
    words = line["words"]
    return bool(words) and words[0]["x0"] < COL_DATA_CONTABILE_MAX_X and bool(DATE_RE.match(words[0]["text"]))


def _build_anchor(line: dict, layout: dict) -> dict:
    words = line["words"]
    date_iso = datetime.strptime(words[0]["text"], "%d/%m/%Y").strftime("%Y-%m-%d")
    col_desc, col_uscite, col_entrate = layout["col_desc"], layout["col_uscite"], layout["col_entrate"]

    inline_desc, uscite, entrate = [], None, None
    for w in words[1:]:
        x0, text = w["x0"], w["text"]
        # Classify by COLUMN first: a money-shaped token that lands in the
        # description column (e.g. the "0,00" in "di cui 0,00 per
        # Commiss/Spese") is descriptive text, not an amount -- only a
        # money-shaped token actually inside the uscite/entrate column is a
        # real amount.
        if col_uscite[0] <= x0 < col_uscite[1] and MONEY_RE.match(text):
            uscite = text
        elif col_entrate[0] <= x0 < col_entrate[1] and MONEY_RE.match(text):
            entrate = text
        elif col_desc[0] <= x0 < col_desc[1]:
            inline_desc.append(text)

    return {"date": date_iso, "inline_desc": inline_desc, "uscite": uscite, "entrate": entrate}


def parse_pdf(pdf_path: str):
    """Returns (account_name, transactions) where transactions is a list of
    {date, description, amount_cents} dicts."""
    transactions = []
    layout = None
    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages):
            lines = extract_lines(page)
            if page_num == 0:
                layout = _detect_layout(lines)
            header_idx = next(
                (i for i, l in enumerate(lines) if "DESCRIZIONE" in l["text"].upper() and "OPERAZION" in l["text"].upper()),
                None,
            )
            if header_idx is None:
                continue
            table_lines = cut_before_marker(lines[header_idx + layout["header_lines_to_skip"] :], "SALDO FINALE")
            rows = group_transaction_rows(
                table_lines, _is_anchor, lambda line, layout=layout: _build_anchor(line, layout)
            )
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
    return layout["account_name"], transactions


def import_pdf(conn: sqlite3.Connection, pdf_path: str) -> dict:
    summary = {"inserted": 0, "duplicates": 0, "categorized": 0, "needs_category": 0, "account_name": None}
    account_name, txs = parse_pdf(pdf_path)
    summary["account_name"] = account_name
    account_id = get_account_id(conn, account_name)
    for tx in txs:
        category = categorize(conn, tx["description"])
        result = add_transaction(
            conn,
            date=tx["date"],
            amount_cents=tx["amount_cents"],
            description=tx["description"],
            source="findomestic_pdf",
            account_id=account_id,
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
    parser = argparse.ArgumentParser(description="Importa un estratto conto Findomestic (PDF, corrente o deposito).")
    parser.add_argument("pdf_path")
    parser.add_argument("--db", default=app_config.DB_PATH)
    args = parser.parse_args()

    conn = db.connect(args.db)
    db.init_schema(conn)
    summary = import_pdf(conn, args.pdf_path)
    print(
        f"Conto rilevato: {summary['account_name']}. "
        f"Importate {summary['inserted']} righe nuove "
        f"({summary['categorized']} categorizzate, {summary['needs_category']} da categorizzare), "
        f"{summary['duplicates']} duplicati ignorati."
    )


if __name__ == "__main__":
    main()
