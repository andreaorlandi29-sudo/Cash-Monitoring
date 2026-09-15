import sqlite3

import pytest

from cashmon import db


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    db.init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def seeded_conn(conn):
    conn.execute(
        "INSERT INTO accounts (name, initial_balance_cents, initial_balance_date) VALUES (?, ?, ?)",
        ("Test Account", 1_000_000, "2026-01-01"),
    )
    conn.commit()
    return conn


@pytest.fixture
def account_id(seeded_conn):
    return seeded_conn.execute("SELECT id FROM accounts WHERE name = 'Test Account'").fetchone()["id"]
