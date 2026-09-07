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
        "INSERT INTO config (key, value) VALUES ('initial_balance_cents', '1000000')"
    )
    conn.execute(
        "INSERT INTO config (key, value) VALUES ('initial_balance_date', '2026-01-01')"
    )
    conn.commit()
    return conn
