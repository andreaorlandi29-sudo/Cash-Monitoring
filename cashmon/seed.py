"""Initialize the database: create the schema, set an account's opening
balance, and load the default categorization rules. Safe to re-run
(idempotent) -- re-running with the same --account just updates its opening
balance/date rather than creating a duplicate.

Run once per account you want to track (e.g. once for the checking account,
once for a linked deposit account) -- each tracks its own balance, and
"patrimonio" (total net worth) is the sum of all of them.
"""
import argparse
from datetime import date

from cashmon import config as app_config
from cashmon import db
from cashmon.categorizer import seed_rules

DEFAULT_INITIAL_BALANCE_EUR = 10000.0
# Must match db.LEGACY_ACCOUNT_NAME -- this is the account name a database
# created before multi-account support gets migrated into, so re-running
# seed with default settings continues to mean the same account.
DEFAULT_ACCOUNT_NAME = "Findomestic Conto Corrente"


def seed(db_path: str, initial_balance_eur: float, initial_balance_date: str, account_name: str) -> None:
    conn = db.connect(db_path)
    db.init_schema(conn)

    cents = round(initial_balance_eur * 100)
    existing = conn.execute("SELECT id FROM accounts WHERE name = ?", (account_name,)).fetchone()
    if existing:
        conn.execute(
            "UPDATE accounts SET initial_balance_cents = ?, initial_balance_date = ? WHERE id = ?",
            (cents, initial_balance_date, existing["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO accounts (name, initial_balance_cents, initial_balance_date) VALUES (?, ?, ?)",
            (account_name, cents, initial_balance_date),
        )
    conn.commit()
    seed_rules(conn)
    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Inizializza (o aggiorna) un conto nel database cashmon.")
    parser.add_argument("--db", default=app_config.DB_PATH)
    parser.add_argument("--account", default=DEFAULT_ACCOUNT_NAME, help="Nome del conto (es. 'Findomestic Conto Deposito')")
    parser.add_argument("--balance", type=float, default=DEFAULT_INITIAL_BALANCE_EUR, help="Saldo iniziale in euro")
    parser.add_argument("--date", default=date.today().isoformat(), help="Data del saldo iniziale (YYYY-MM-DD)")
    args = parser.parse_args()

    seed(args.db, args.balance, args.date, args.account)
    print(f"Conto '{args.account}' inizializzato in {args.db} con saldo di {args.balance:.2f} EUR al {args.date}.")


if __name__ == "__main__":
    main()
