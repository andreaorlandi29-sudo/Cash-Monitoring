from cashmon.importers.pdf_common import group_transaction_rows
from cashmon.importers.pdf_findomestic import LAYOUTS, _build_anchor, _detect_layout, _is_anchor
from tests.pdf_helpers import line

CORRENTE = LAYOUTS["corrente"]
DEPOSITO = LAYOUTS["deposito"]


def test_is_anchor_requires_leading_date_in_left_column():
    assert _is_anchor(line(1, [(34, "03/08/2026"), (91, "31/07/2026"), (156, "34")]))
    assert not _is_anchor(line(1, [(183, "Addebito"), (219, "diretto")]))


def test_build_anchor_self_contained_row():
    l = line(
        1,
        [
            (34, "03/08/2026"),
            (91, "31/07/2026"),
            (156, "34"),
            (183, "Addebito"),
            (219, "Imp.di"),
            (460, "1.504,77"),
            (498, "€"),
        ],
    )
    anchor = _build_anchor(l, CORRENTE)
    assert anchor["date"] == "2026-08-03"
    assert anchor["inline_desc"] == ["Addebito", "Imp.di"]
    assert anchor["uscite"] == "1.504,77"
    assert anchor["entrate"] is None


def test_build_anchor_entrata_row():
    l = line(1, [(34, "13/08/2026"), (91, "13/08/2026"), (156, "48"), (513, "300,00"), (556, "€")])
    anchor = _build_anchor(l, CORRENTE)
    assert anchor["entrate"] == "300,00"
    assert anchor["uscite"] is None


def test_full_pre_anchor_post_row_reconstructs_description_and_amount():
    # "Addebito diretto a fav. di FINDOMESTIC BANCA S.P.A. di cui 0,00" /
    # anchor / "per Commiss/Spese" -- the real 3-line pattern from the sample.
    lines = [
        line(1, [(183, "Addebito"), (219, "diretto"), (245, "a"), (277, "fav."), (298, "di"), (315, "FINDOMESTIC")]),
        line(2, [(34, "05/08/2026"), (91, "05/08/2026"), (156, "15"), (473, "41,66"), (498, "€")]),
        line(3, [(183, "per"), (198, "Commiss/Spese")]),
    ]
    rows = group_transaction_rows(lines, _is_anchor, lambda l: _build_anchor(l, CORRENTE))
    assert len(rows) == 1
    assert rows[0]["uscite"] == "41,66"
    assert "FINDOMESTIC" in rows[0]["desc_words"]
    assert rows[0]["desc_words"][-1] == "Commiss/Spese"


def test_deposito_layout_has_no_causale_column_and_different_description_start():
    # Real deposit-account row: no Causale column, description starts right
    # after Data Valuta (x0=149.8), entrata column further left than corrente.
    l = line(
        1,
        [
            (35.5, "13/08/2026"),
            (95.0, "11/08/2026"),
            (149.8, "Utilizzo"),
            (179.3, "Carta"),
            (536.4, "2,02"),
            (556.4, "€"),
        ],
    )
    anchor = _build_anchor(l, DEPOSITO)
    assert anchor["inline_desc"] == ["Utilizzo", "Carta"]
    assert anchor["entrate"] == "2,02"
    assert anchor["uscite"] is None


def test_detect_layout_picks_deposito_from_title_line():
    first_page_lines = [line(1, [(0, "Estratto"), (1, "Conto"), (2, "Deposito"), (3, "n°"), (4, "8/2026")])]
    assert _detect_layout(first_page_lines) is DEPOSITO


def test_detect_layout_picks_corrente_by_default():
    first_page_lines = [line(1, [(0, "Estratto"), (1, "Conto"), (2, "n°"), (3, "8/2026")])]
    assert _detect_layout(first_page_lines) is CORRENTE
