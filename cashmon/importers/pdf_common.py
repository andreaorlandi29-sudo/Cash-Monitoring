"""Shared parsing helpers for statement PDFs (Findomestic, Nexi).

Both statements lay out each transaction as a short block of one to three
physical text lines: a single "anchor" line carrying the date (and, for
Findomestic, the causale/amounts) plus zero or more description-only lines
that appear either just before or just after the anchor line, depending on
how much text the description needs. Which side a description-only line
belongs to is inferred from whether the *next* anchor line already carries
its own inline description -- see `group_transaction_rows`.
"""
import re
from typing import Callable, List

MONEY_RE = re.compile(r"^-?\d{1,3}(?:\.\d{3})*,\d{2}$")
FOREIGN_AMOUNT_RE = re.compile(r"^-?\d{1,3}(?:\.\d{3})*,\d{2}[A-Z]{2,4}$")
RATIO_RE = re.compile(r"^-?\d+,\d{4,}$")


def looks_like_amount(text: str) -> bool:
    """True for a plain euro amount, a foreign-currency amount with an
    attached currency code (e.g. '24,40USD'), or an exchange-rate ratio
    (many decimal digits, e.g. '0,857044') -- none of these are description
    text."""
    return bool(MONEY_RE.match(text) or FOREIGN_AMOUNT_RE.match(text) or RATIO_RE.match(text))


def extract_lines(page, x_tolerance: float = 1.5) -> List[dict]:
    """Groups a page's words into physical lines (by shared vertical
    position), sorted top-to-bottom and left-to-right within each line."""
    words = page.extract_words(x_tolerance=x_tolerance)
    buckets: dict = {}
    for w in words:
        key = round(w["top"])
        buckets.setdefault(key, []).append(w)
    lines = []
    for top in sorted(buckets):
        row_words = sorted(buckets[top], key=lambda w: w["x0"])
        lines.append({"top": top, "words": row_words, "text": " ".join(w["text"] for w in row_words)})
    return lines


def parse_italian_amount_to_cents(raw: str) -> int:
    """'1.504,77' -> 150477. Sign is preserved (a leading '-' means a credit
    on a card statement)."""
    normalized = raw.replace(".", "").replace(",", ".")
    return round(float(normalized) * 100)


def clean_description(words: List[str]) -> str:
    """Best-effort cosmetic cleanup for PDFs whose text layer lacks spaces
    between words (e.g. 'GuinnessStorehouse' -> 'Guinness Storehouse').
    Categorization matching is substring-based and case-insensitive, so this
    never affects which rule matches -- it's purely for a readable
    description string."""
    text = " ".join(words)
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    return " ".join(text.split())


def group_transaction_rows(
    lines: List[dict],
    is_anchor: Callable[[dict], bool],
    build_anchor: Callable[[dict], dict],
) -> List[dict]:
    """Generic pre/anchor/post row grouping (see module docstring).

    `build_anchor(line)` must return a dict with an `inline_desc` list of
    description words found ON the anchor line itself (possibly empty), plus
    whatever other fields the caller needs (date, amounts, ...). This
    function adds `desc_words`: the inline words plus any pending
    description-only lines, attached to whichever row they belong to.
    """
    rows: List[dict] = []
    pending: List[dict] = []
    # True from the moment a row is appended until the first non-anchor line
    # is seen. Combined with `prev_anchor_had_empty_inline`, a SHORT line
    # arriving in that window is treated as that row's own trailing
    # annotation (e.g. "Commiss/Spese") rather than a lead-in for whatever
    # comes next -- but ONLY right after a row that itself needed lead-in
    # lines (empty inline description). That restriction matters: a row
    # whose anchor already carried its own full description never has a
    # real trailing line in these statements, so without it a short,
    # single-word lead-in for the *next* row (e.g. a one-word merchant name
    # on its own line before a foreign-currency purchase) would wrongly get
    # glued onto the row above instead of starting the next one. Two
    # adjacent rows that both need lead-in lines, on the other hand, have no
    # other way to tell "this line closes the row above" from "this line
    # opens the row below" -- this is what resolves that case correctly.
    just_appended = False
    prev_anchor_had_empty_inline = False

    def flush_trailing():
        if rows and pending:
            for line in pending:
                rows[-1]["desc_words"].extend(w["text"] for w in line["words"])
        pending.clear()

    for line in lines:
        if is_anchor(line):
            anchor = build_anchor(line)
            empty_inline = not anchor["inline_desc"]
            if not empty_inline:
                # Self-contained row: any buffered lines were trailing text
                # for the PREVIOUS row, not a lead-in for this one.
                flush_trailing()
                anchor["desc_words"] = list(anchor["inline_desc"])
            else:
                # This row needed lead-in lines; they're the pending lines
                # sitting right before this anchor.
                anchor["desc_words"] = [w["text"] for pl in pending for w in pl["words"]]
                pending.clear()
            rows.append(anchor)
            just_appended = True
            prev_anchor_had_empty_inline = empty_inline
        else:
            if (
                just_appended
                and prev_anchor_had_empty_inline
                and rows
                and not pending
                and len(line["text"]) <= 40
            ):
                rows[-1]["desc_words"].extend(w["text"] for w in line["words"])
            else:
                pending.append(line)
            just_appended = False

    # Anything left over at the very end sits after the last real row with no
    # further anchor to resolve it against -- typically legal boilerplate or
    # a promo block, not part of any transaction. Keep at most one short
    # trailing line (a genuine continuation, e.g. "Commiss/Spese"); discard
    # longer leftovers rather than gluing unrelated footer text onto the last
    # row's description.
    if rows and pending:
        first = pending[0]
        if len(first["text"]) <= 40:
            rows[-1]["desc_words"].extend(w["text"] for w in first["words"])
    pending.clear()

    return rows


def cut_before_marker(lines: List[dict], marker: str) -> List[dict]:
    """Drops `marker` (matched case-insensitively, ignoring spaces) and
    everything after it on this page -- these statements end their
    transaction table with a summary line (e.g. 'SALDO FINALE', 'TOTALE
    SPESE'). Returns the same list unchanged if the marker isn't present."""
    marker_norm = marker.replace(" ", "").upper()
    out = []
    for line in lines:
        if line["text"].replace(" ", "").upper().startswith(marker_norm):
            break
        out.append(line)
    return out
