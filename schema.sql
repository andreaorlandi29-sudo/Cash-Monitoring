-- Cash Monitoring schema. Money is always stored as integer cents.

CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Actual, real-world movements (bank statement imports, confirmed telegram entries,
-- the opening balance). superseded_by_id lets a provisional row (e.g. an iPhone
-- notification) be replaced by the authoritative statement row without deleting
-- either: only rows with superseded_by_id IS NULL count towards the balance.
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,                    -- ISO 8601 YYYY-MM-DD
    amount_cents INTEGER NOT NULL,         -- positive = entrata, negative = uscita
    description TEXT NOT NULL,
    category TEXT,                         -- NULL means "needs categorization"
    status TEXT NOT NULL DEFAULT 'confirmed'
        CHECK (status IN ('provisional', 'confirmed')),
    source TEXT NOT NULL,                  -- 'seed' | 'csv_import' | 'telegram' | 'nexi_card' | 'findomestic_pdf'
    import_hash TEXT UNIQUE,               -- dedup key; NULL allowed for rows that don't need it
    superseded_by_id INTEGER REFERENCES transactions(id),
    -- 0 for rows that are itemized spend detail but not a separate movement of
    -- money out of the checking account (e.g. a Nexi credit-card line item --
    -- Nexi settles the whole month in one lump SDD debit ~2 months later,
    -- which is imported separately, from the bank statement, WITH this flag
    -- at 1). Recording both would double-count the same money.
    counts_toward_balance INTEGER NOT NULL DEFAULT 1 CHECK (counts_toward_balance IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(date);

-- Expected future movements. A projection stops contributing to the forecast once
-- it is matched to a real transaction (matched_transaction_id set) -- it is never
-- deleted, so forecast-vs-actual can still be compared later.
CREATE TABLE IF NOT EXISTS projections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    description TEXT NOT NULL,
    category TEXT,
    matched_transaction_id INTEGER REFERENCES transactions(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_projections_date ON projections(date);

-- Auto-categorization rules. `pattern` is matched as a substring against the
-- normalized description. Lower priority number = checked first. `source`
-- distinguishes seed rules from ones learned from a user's Telegram answer.
CREATE TABLE IF NOT EXISTS categorization_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern TEXT NOT NULL,
    category TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'seed' CHECK (source IN ('seed', 'learned')),
    priority INTEGER NOT NULL DEFAULT 100,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (pattern, category)
);

-- Tracks a categorization question sent to Telegram so the callback answer can
-- be routed back to the right transaction even after a bot restart.
CREATE TABLE IF NOT EXISTS pending_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id INTEGER NOT NULL REFERENCES transactions(id),
    chat_id INTEGER NOT NULL,
    message_id INTEGER,
    asked_at TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at TEXT
);
