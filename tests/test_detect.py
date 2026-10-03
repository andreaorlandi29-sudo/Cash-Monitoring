"""Tests detect_pdf_kind's routing against fake pdfplumber-shaped pages (a
real PDF file isn't needed -- pdfplumber.open is monkeypatched, mirroring how
the rest of the importer tests avoid real PDF fixtures)."""
from contextlib import contextmanager

import cashmon.importers.detect as detect_module
from cashmon.importers.detect import detect_pdf_kind


class FakePage:
    def __init__(self, text):
        self._text = text

    def extract_text(self):
        return self._text


class FakePdf:
    def __init__(self, pages):
        self.pages = pages


def _patch_pdfplumber(monkeypatch, pages):
    @contextmanager
    def fake_open(path):
        yield FakePdf(pages)

    monkeypatch.setattr(detect_module.pdfplumber, "open", fake_open)


def test_detects_nexi_from_table_header(monkeypatch):
    _patch_pdfplumber(monkeypatch, [FakePage("Dettaglio dei Suoi movimenti\nData Descrizione Importo in Euro")])
    assert detect_pdf_kind("fake.pdf") == "nexi"


def test_detects_findomestic_from_column_header(monkeypatch):
    _patch_pdfplumber(monkeypatch, [FakePage("Data Contabile Descrizione dell'operazione Uscite Entrate")])
    assert detect_pdf_kind("fake.pdf") == "findomestic"


def test_checks_every_page_not_just_the_first(monkeypatch):
    _patch_pdfplumber(monkeypatch, [FakePage("Estratto Conto n. 8/2026"), FakePage("Descrizione delle operazioni")])
    assert detect_pdf_kind("fake.pdf") == "findomestic"


def test_unrecognized_pdf_returns_none(monkeypatch):
    _patch_pdfplumber(monkeypatch, [FakePage("Ricevuta di pagamento qualunque")])
    assert detect_pdf_kind("fake.pdf") is None


def test_page_with_no_extractable_text_does_not_crash(monkeypatch):
    _patch_pdfplumber(monkeypatch, [FakePage(None)])
    assert detect_pdf_kind("fake.pdf") is None
