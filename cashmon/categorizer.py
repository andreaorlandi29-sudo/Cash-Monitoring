"""Rule-based auto-categorization. The rule set doubles as the "learning" store:
answers given via Telegram are inserted as new rows with source='learned' and a
higher priority than the seed defaults, so the same merchant is never asked
about twice."""
import sqlite3
from typing import Optional

from cashmon.ledger import normalize_description

LEARNED_PRIORITY = 50
SEED_PRIORITY = 100

# Displayed to the user and used as inline-keyboard buttons in the bot.
CATEGORIES = [
    "Alimentari",
    "Trasporti",
    "Casa",
    "Utenze",
    "Salute",
    "Svago",
    "Stipendio",
    "Bonifico",
    "Altro",
]

# (pattern, category, priority) -- pattern is matched as a substring against the
# normalized (uppercased, whitespace-collapsed) description.
SEED_RULES = [
    ("ESSELUNGA", "Alimentari", SEED_PRIORITY),
    ("CONAD", "Alimentari", SEED_PRIORITY),
    ("COOP", "Alimentari", SEED_PRIORITY),
    ("CARREFOUR", "Alimentari", SEED_PRIORITY),
    ("LIDL", "Alimentari", SEED_PRIORITY),
    ("EUROSPIN", "Alimentari", SEED_PRIORITY),
    ("PAM", "Alimentari", SEED_PRIORITY),
    ("TRENITALIA", "Trasporti", SEED_PRIORITY),
    ("TRENORD", "Trasporti", SEED_PRIORITY),
    ("ITALO", "Trasporti", SEED_PRIORITY),
    ("ATM MILANO", "Trasporti", SEED_PRIORITY),
    ("AUTOSTRADE", "Trasporti", SEED_PRIORITY),
    ("TELEPASS", "Trasporti", SEED_PRIORITY),
    ("ENI STATION", "Trasporti", SEED_PRIORITY),
    ("Q8", "Trasporti", SEED_PRIORITY),
    ("ENEL ENERGIA", "Utenze", SEED_PRIORITY),
    ("A2A", "Utenze", SEED_PRIORITY),
    ("HERA", "Utenze", SEED_PRIORITY),
    ("TIM", "Utenze", SEED_PRIORITY),
    ("VODAFONE", "Utenze", SEED_PRIORITY),
    ("WINDTRE", "Utenze", SEED_PRIORITY),
    ("FASTWEB", "Utenze", SEED_PRIORITY),
    ("AFFITTO", "Casa", SEED_PRIORITY),
    ("CONDOMINIO", "Casa", SEED_PRIORITY),
    ("MUTUO", "Casa", SEED_PRIORITY),
    ("FARMACIA", "Salute", SEED_PRIORITY),
    ("NETFLIX", "Svago", SEED_PRIORITY),
    ("SPOTIFY", "Svago", SEED_PRIORITY),
    ("AMAZON PRIME", "Svago", SEED_PRIORITY),
    ("STIPENDIO", "Stipendio", SEED_PRIORITY),
    ("BONIFICO", "Bonifico", SEED_PRIORITY + 100),  # generic fallback, checked last
]


def seed_rules(conn: sqlite3.Connection) -> None:
    for pattern, category, priority in SEED_RULES:
        conn.execute(
            """
            INSERT OR IGNORE INTO categorization_rules (pattern, category, source, priority)
            VALUES (?, ?, 'seed', ?)
            """,
            (pattern, category, priority),
        )
    conn.commit()


def categorize(conn: sqlite3.Connection, description: str) -> Optional[str]:
    normalized = normalize_description(description)
    rules = conn.execute(
        "SELECT pattern, category FROM categorization_rules ORDER BY priority ASC, id ASC"
    ).fetchall()
    for rule in rules:
        if rule["pattern"] in normalized:
            return rule["category"]
    return None


def learn_rule(conn: sqlite3.Connection, description: str, category: str) -> None:
    """Record a user's manual category choice so future matching descriptions
    are categorized automatically."""
    normalized = normalize_description(description)
    conn.execute(
        """
        INSERT OR IGNORE INTO categorization_rules (pattern, category, source, priority)
        VALUES (?, ?, 'learned', ?)
        """,
        (normalized, category, LEARNED_PRIORITY),
    )
    conn.commit()
