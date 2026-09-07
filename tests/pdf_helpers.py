"""Builders for synthetic pdfplumber-shaped line/word dicts, used to unit-test
the PDF row-grouping logic without needing real PDF fixtures."""


def word(x0, text):
    return {"x0": x0, "text": text}


def line(top, word_specs):
    """word_specs: list of (x0, text) tuples."""
    words = [word(x0, text) for x0, text in word_specs]
    return {"top": top, "words": words, "text": " ".join(w["text"] for w in words)}
