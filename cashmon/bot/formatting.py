"""Shared formatting helpers (kept separate from telegram_bot.py so they can be
unit-tested without the python-telegram-bot dependency installed)."""


def format_eur(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    euros, remainder = divmod(cents, 100)
    formatted_int = f"{euros:,}".replace(",", ".")
    return f"{sign}{formatted_int},{remainder:02d} €"


def parse_amount_to_cents(raw: str) -> int:
    """Parses a user-typed amount like '12,50' or '12.50' or '1200' into cents."""
    normalized = raw.strip().replace(".", "").replace(",", ".") if "," in raw else raw.strip()
    return round(float(normalized) * 100)
