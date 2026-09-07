from cashmon.categorizer import categorize, learn_rule, seed_rules
from cashmon.ledger import normalize_description


def test_normalize_description_collapses_case_and_whitespace():
    assert normalize_description("  esselunga   milano  ") == "ESSELUNGA MILANO"


def test_categorize_matches_seed_rule(conn):
    seed_rules(conn)
    assert categorize(conn, "ESSELUNGA VIA ROMA") == "Alimentari"
    assert categorize(conn, "Pagamento Netflix.com") == "Svago"


def test_categorize_returns_none_when_no_rule_matches(conn):
    seed_rules(conn)
    assert categorize(conn, "Negozio Sconosciuto XYZ") is None


def test_learn_rule_makes_future_matches_succeed(conn):
    seed_rules(conn)
    assert categorize(conn, "Negozio Sconosciuto XYZ") is None
    learn_rule(conn, "Negozio Sconosciuto XYZ", "Altro")
    assert categorize(conn, "Negozio Sconosciuto XYZ") == "Altro"


def test_learned_rule_wins_over_generic_seed_fallback(conn):
    seed_rules(conn)
    # BONIFICO is a low-priority generic fallback; a learned rule for a more
    # specific description should still match before it if it's a substring
    # that also matches BONIFICO -- but priority determines the winner here.
    learn_rule(conn, "BONIFICO DA MARIO ROSSI", "Stipendio")
    assert categorize(conn, "BONIFICO DA MARIO ROSSI") == "Stipendio"
