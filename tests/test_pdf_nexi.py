from cashmon.importers.pdf_common import group_transaction_rows
from cashmon.importers.pdf_nexi import _build_anchor, _is_anchor
from tests.pdf_helpers import line


def test_is_anchor_two_digit_year_date():
    assert _is_anchor(line(1, [(32, "31/07/26"), (73, "Octopus")]))
    assert not _is_anchor(line(1, [(32, "Octopus"), (73, "Electroverse")]))


def test_build_anchor_plain_row():
    l = line(1, [(32, "31/07/26"), (73, "OctopusElectroverseLondonG"), (368, "6,53")])
    anchor = _build_anchor(l)
    assert anchor["date"] == "2026-07-31"
    assert anchor["euro_amount"] == "6,53"
    assert anchor["inline_desc"] == ["OctopusElectroverseLondonG"]


def test_build_anchor_foreign_currency_row_picks_leftmost_plain_amount():
    # "22/08/26 21,43 24,40USD 0,857044" -- euro amount is the leftmost plain
    # money token; the foreign amount and exchange ratio must be excluded.
    l = line(1, [(32, "22/08/26"), (340, "21,43"), (415, "24,40USD"), (527, "0,857044")])
    anchor = _build_anchor(l)
    assert anchor["euro_amount"] == "21,43"
    assert anchor["inline_desc"] == []


def test_pro_trial_three_line_pattern_with_commission_annotation_filtered():
    # Real sample: description-only line, then anchor with no inline desc
    # (foreign-currency amounts only), then a commission annotation line that
    # the importer filters out before grouping (simulated here by omitting it,
    # since that filtering happens in parse_pdf, not group_transaction_rows).
    lines = [
        line(1, [(73, "ProTrialOverSanFranciscoU")]),
        line(2, [(32, "22/08/26"), (340, "21,43"), (415, "24,40USD"), (527, "0,857044")]),
    ]
    rows = group_transaction_rows(lines, _is_anchor, _build_anchor)
    assert len(rows) == 1
    assert rows[0]["desc_words"] == ["ProTrialOverSanFranciscoU"]
    assert rows[0]["euro_amount"] == "21,43"


def test_two_separate_same_day_rows_stay_separate():
    l1 = line(1, [(32, "24/08/26"), (73, "AnthropicSanFranciscoU"), (340, "6,10")])
    l2 = line(2, [(32, "24/08/26"), (73, "AnthropicSanFranciscoU"), (340, "9,99")])
    rows = group_transaction_rows([l1, l2], _is_anchor, _build_anchor)
    assert len(rows) == 2
    assert rows[0]["euro_amount"] == "6,10"
    assert rows[1]["euro_amount"] == "9,99"
