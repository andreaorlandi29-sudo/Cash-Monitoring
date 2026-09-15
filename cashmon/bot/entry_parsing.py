"""Pure parsing for the Telegram free-text entry formats, kept separate from
telegram_bot.py so it can be unit-tested without the python-telegram-bot
dependency (mirrors formatting.py).

Formats:
  "spesa 12,50 Esselunga"            -- normal expense, hits the checking
                                         account balance immediately
  "entrata 1200 Stipendio"           -- normal income
  "satispay spesa 12,50 Bar"         -- Satispay purchase: real spend for
                                         category reporting, but the money
                                         hasn't left the checking account yet
                                         (Satispay nets weekly; see README)
  "satispay entrata 20 da Mario"     -- P2P payment received via Satispay:
                                         doesn't land in the checking account
                                         either -- it's usable Satispay
                                         credit until next week's netting
  "previsione spesa 150 il 2026-10-05 Rata condominio"
                                     -- a planned future expense/income for a
                                        known date (one date per message --
                                        four installments is four messages)
  "previsione saldo al 2026-12-01"  -- asks for a projected balance (see
                                        ledger.forecast_breakdown)
"""
import re
from dataclasses import dataclass

from cashmon.bot.formatting import parse_amount_to_cents

ENTRY_RE = re.compile(r"^(?:(satispay)\s+)?(spesa|entrata)\s+([\d.,]+)\s+(.+)$", re.IGNORECASE)
PROJECTION_RE = re.compile(r"^previsione\s+(spesa|entrata)\s+([\d.,]+)\s+il\s+(\d{4}-\d{2}-\d{2})\s+(.+)$", re.IGNORECASE)
BALANCE_FORECAST_RE = re.compile(r"^previsione\s+saldo\s+al\s+(\d{4}-\d{2}-\d{2})$", re.IGNORECASE)


@dataclass
class ParsedEntry:
    amount_cents: int  # signed: negative for spesa, positive for entrata
    description: str
    source: str  # 'telegram' | 'satispay'
    counts_toward_balance: int  # 1 for a real checking-account movement, 0 otherwise


def parse_entry(text: str):
    """Returns a ParsedEntry, or None if `text` doesn't match a known format."""
    match = ENTRY_RE.match(text.strip())
    if not match:
        return None

    satispay_prefix, kind, amount_raw, description = match.groups()
    try:
        cents = parse_amount_to_cents(amount_raw)
    except ValueError:
        return None
    cents = -abs(cents) if kind.lower() == "spesa" else abs(cents)

    if satispay_prefix:
        return ParsedEntry(amount_cents=cents, description=description, source="satispay", counts_toward_balance=0)
    return ParsedEntry(amount_cents=cents, description=description, source="telegram", counts_toward_balance=1)


@dataclass
class ParsedProjection:
    date: str  # ISO 8601 YYYY-MM-DD
    amount_cents: int  # signed: negative for spesa, positive for entrata
    description: str


def parse_projection(text: str):
    """Returns a ParsedProjection, or None if `text` doesn't match. A plain
    "spesa"/"entrata" (no "previsione" prefix) never matches this -- the two
    formats are mutually exclusive by construction, not by handler ordering."""
    match = PROJECTION_RE.match(text.strip())
    if not match:
        return None

    kind, amount_raw, date_str, description = match.groups()
    try:
        cents = parse_amount_to_cents(amount_raw)
    except ValueError:
        return None
    cents = -abs(cents) if kind.lower() == "spesa" else abs(cents)
    return ParsedProjection(date=date_str, amount_cents=cents, description=description)


def parse_balance_forecast_request(text: str):
    """Returns the target date (YYYY-MM-DD) for a "previsione saldo al ..."
    request, or None if `text` doesn't match."""
    match = BALANCE_FORECAST_RE.match(text.strip())
    return match.group(1) if match else None
