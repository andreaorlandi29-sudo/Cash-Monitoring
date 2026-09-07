from cashmon.importers.pdf_common import (
    cut_before_marker,
    group_transaction_rows,
    looks_like_amount,
    parse_italian_amount_to_cents,
)
from tests.pdf_helpers import line


def is_anchor(l):
    return l["words"] and l["words"][0]["text"].isdigit()


def build_anchor(l):
    inline = [w["text"] for w in l["words"][1:]]
    return {"id": l["words"][0]["text"], "inline_desc": inline}


def test_looks_like_amount():
    assert looks_like_amount("1.504,77")
    assert looks_like_amount("41,66")
    assert looks_like_amount("24,40USD")
    assert looks_like_amount("0,857044")
    assert not looks_like_amount("Esselunga")
    assert not looks_like_amount("9495")


def test_parse_italian_amount_to_cents():
    assert parse_italian_amount_to_cents("1.504,77") == 150477
    assert parse_italian_amount_to_cents("41,66") == 4166
    assert parse_italian_amount_to_cents("-15,00") == -1500


def test_group_self_contained_row():
    lines = [line(1, [(0, "1"), (1, "Full"), (2, "description"), (3, "here")])]
    rows = group_transaction_rows(lines, is_anchor, build_anchor)
    assert len(rows) == 1
    assert rows[0]["desc_words"] == ["Full", "description", "here"]


def test_group_pre_anchor_post_pattern():
    # Mirrors the real Findomestic layout: description text before AND after
    # the anchor line, anchor line itself carrying no inline description.
    lines = [
        line(1, [(0, "Addebito"), (1, "diretto"), (2, "a"), (3, "fav.")]),
        line(2, [(0, "2")]),
        line(3, [(0, "Commiss/Spese")]),
    ]
    rows = group_transaction_rows(lines, is_anchor, build_anchor)
    assert len(rows) == 1
    assert rows[0]["desc_words"] == ["Addebito", "diretto", "a", "fav.", "Commiss/Spese"]


def test_group_two_pre_lines_no_post():
    # Mirrors the AXXAM case: two lead-in lines, nothing after the anchor.
    lines = [
        line(1, [(0, "Emolumenti"), (1, "del"), (2, "31.08.26")]),
        line(2, [(0, "COMPETENZE"), (1, "MESE")]),
        line(3, [(0, "3")]),  # anchor, empty inline desc
    ]
    rows = group_transaction_rows(lines, is_anchor, build_anchor)
    assert len(rows) == 1
    assert rows[0]["desc_words"] == ["Emolumenti", "del", "31.08.26", "COMPETENZE", "MESE"]


def test_group_back_to_back_self_contained_rows_dont_bleed_into_each_other():
    lines = [
        line(1, [(0, "1"), (1, "Row"), (2, "one"), (3, "description")]),
        line(2, [(0, "2"), (1, "Row"), (2, "two"), (3, "description")]),
    ]
    rows = group_transaction_rows(lines, is_anchor, build_anchor)
    assert len(rows) == 2
    assert rows[0]["desc_words"] == ["Row", "one", "description"]
    assert rows[1]["desc_words"] == ["Row", "two", "description"]


def test_trailing_long_footer_is_discarded_not_glued_to_last_row():
    lines = [
        line(1, [(0, "1"), (1, "Self contained row")]),
        line(2, [(0, "This is a long legal disclaimer that goes on and on and on")]),
    ]
    rows = group_transaction_rows(lines, is_anchor, build_anchor)
    assert len(rows) == 1
    assert "disclaimer" not in " ".join(rows[0]["desc_words"])


def test_two_consecutive_empty_inline_anchors_dont_swap_their_suffixes():
    # Regression: two rows in a row that both need lead-in lines (empty
    # inline description). The short suffix right after each anchor must
    # stay attached to THAT anchor, not bleed into the next row's lead-in.
    lines = [
        line(1, [(0, "Addebito"), (1, "TELECOM"), (2, "di"), (3, "cui"), (4, "0,00"), (5, "per")]),
        line(2, [(0, "10")]),  # anchor 1, empty inline
        line(3, [(0, "Commiss/Spese")]),
        line(4, [(0, "Addebito"), (1, "OCTOPUS"), (2, "di"), (3, "cui"), (4, "0,00")]),
        line(5, [(0, "11")]),  # anchor 2, empty inline
        line(6, [(0, "per"), (1, "Commiss/Spese")]),
    ]
    rows = group_transaction_rows(lines, is_anchor, build_anchor)
    assert len(rows) == 2
    assert rows[0]["desc_words"] == ["Addebito", "TELECOM", "di", "cui", "0,00", "per", "Commiss/Spese"]
    assert rows[1]["desc_words"] == ["Addebito", "OCTOPUS", "di", "cui", "0,00", "per", "Commiss/Spese"]


def test_short_lead_in_after_self_contained_row_is_not_glued_to_it():
    # Regression: a self-contained row (non-empty inline desc) is never
    # followed by a real trailing line in these statements -- a short line
    # right after one must be treated as the NEXT row's lead-in, not glued
    # onto the row above (this is what broke Nexi's one-word merchant names
    # sitting alone before a foreign-currency purchase row).
    lines = [
        line(1, [(0, "1"), (1, "Self"), (2, "contained"), (3, "row")]),
        line(2, [(0, "OneWordMerchant")]),  # short, but a lead-in for row 2
        line(3, [(0, "2")]),  # anchor, empty inline -- needs that lead-in
    ]
    rows = group_transaction_rows(lines, is_anchor, build_anchor)
    assert len(rows) == 2
    assert rows[0]["desc_words"] == ["Self", "contained", "row"]
    assert rows[1]["desc_words"] == ["OneWordMerchant"]


def test_trailing_short_line_is_kept():
    lines = [
        line(1, [(0, "1"), (1, "Self contained row")]),
        line(2, [(0, "Commiss/Spese")]),
    ]
    rows = group_transaction_rows(lines, is_anchor, build_anchor)
    assert rows[0]["desc_words"][-1] == "Commiss/Spese"


def test_cut_before_marker():
    lines = [
        line(1, [(0, "31/08/2026"), (1, "43"), (2, "Bar"), (3, "12,00")]),
        line(2, [(0, "SALDO"), (1, "FINALE"), (2, "al"), (3, "31/08/2026"), (4, "+2.780,64")]),
        line(3, [(0, "Legal boilerplate that should never be seen")]),
    ]
    result = cut_before_marker(lines, "SALDO FINALE")
    assert len(result) == 1
    assert result[0]["words"][0]["text"] == "31/08/2026"


def test_cut_before_marker_no_match_returns_all():
    lines = [line(1, [(0, "a")]), line(2, [(0, "b")])]
    assert cut_before_marker(lines, "NOPE") == lines
