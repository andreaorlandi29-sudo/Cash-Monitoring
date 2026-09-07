from cashmon.importers.pdf_common import group_transaction_rows
from cashmon.importers.pdf_findomestic import _build_anchor, _is_anchor
from tests.pdf_helpers import line


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
    anchor = _build_anchor(l)
    assert anchor["date"] == "2026-08-03"
    assert anchor["inline_desc"] == ["Addebito", "Imp.di"]
    assert anchor["uscite"] == "1.504,77"
    assert anchor["entrate"] is None


def test_build_anchor_entrata_row():
    l = line(1, [(34, "13/08/2026"), (91, "13/08/2026"), (156, "48"), (513, "300,00"), (556, "€")])
    anchor = _build_anchor(l)
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
    rows = group_transaction_rows(lines, _is_anchor, _build_anchor)
    assert len(rows) == 1
    assert rows[0]["uscite"] == "41,66"
    assert "FINDOMESTIC" in rows[0]["desc_words"]
    assert rows[0]["desc_words"][-1] == "Commiss/Spese"
