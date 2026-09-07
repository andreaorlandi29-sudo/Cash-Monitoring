"""Initialize the database: create the schema, set the opening balance, and
load the default categorization rules. Safe to re-run (idempotent)."""
import argparse
from datetime import date

from cashmon import config as app_config
from cashmon import db
from cashmon.categorizer import seed_rules

DEFAULT_INITIAL_BALANCE_EUR = 10000.0


def seed(db_path: str, initial_balance_eur: float, initial_balance_date: str) -> None:
    conn = db.connect(db_path)
    db.init_schema(conn)
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('initial_balance_cents', ?)",
        (str(round(initial_balance_eur * 100)),),
    )
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('initial_balance_date', ?)",
        (initial_balance_date,),
    )
    conn.commit()
    seed_rules(conn)
    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize the cashmon database.")
    parser.add_argument("--db", default=app_config.DB_PATH)
    parser.add_argument("--balance", type=float, default=DEFAULT_INITIAL_BALANCE_EUR, help="Saldo iniziale in euro")
    parser.add_argument("--date", default=date.today().isoformat(), help="Data del saldo iniziale (YYYY-MM-DD)")
    args = parser.parse_args()

    seed(args.db, args.balance, args.date)
    print(f"Database inizializzato in {args.db} con saldo iniziale di {args.balance:.2f} EUR al {args.date}.")


if __name__ == "__main__":
    main()
