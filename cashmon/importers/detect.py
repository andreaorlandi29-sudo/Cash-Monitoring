"""Tells a Findomestic account statement from a Nexi card statement so a
Telegram-uploaded PDF can be routed to the right parser explicitly, instead
of trying both and treating "no exception" as success -- both parsers
degrade to zero rows (no error) on a PDF they don't recognize, so a
try-both approach can't tell "wrong parser" from "right parser, empty
statement". Uses the same header markers each parser looks for on its own
transaction table.
"""
from typing import Optional

import pdfplumber

NEXI_MARKER = "DETTAGLIODEISUOIMOVIMENTI"


def detect_pdf_kind(pdf_path: str) -> Optional[str]:
    """Returns 'findomestic', 'nexi', or None if neither marker is found on
    any page."""
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = (page.extract_text() or "").upper()
            if NEXI_MARKER in text.replace(" ", ""):
                return "nexi"
            if "DESCRIZIONE" in text and "OPERAZION" in text:
                return "findomestic"
    return None
