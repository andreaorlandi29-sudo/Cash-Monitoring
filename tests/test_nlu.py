"""Tests only the pure conversion from Claude's tool-call output to NluResult
-- no network access, mirrors entry_parsing.py's testable-without-the-SDK
split. See nlu.interpret for the actual API call, which needs a live
ANTHROPIC_API_KEY and isn't covered here."""
from cashmon.bot.nlu import _result_from_tool_input


def test_unrecognized_carries_the_reason():
    result = _result_from_tool_input(
        {"riconosciuto": False, "tipo": "non_pertinente", "motivo_non_chiaro": "importo non specificato"}
    )
    assert result.recognized is False
    assert result.reason_unclear == "importo non specificato"


def test_movimento_spesa_is_negative():
    result = _result_from_tool_input(
        {
            "riconosciuto": True,
            "tipo": "movimento",
            "segno": "spesa",
            "importo_euro": 12.5,
            "descrizione": "Esselunga",
            "data": "2026-09-20",
        }
    )
    assert result.recognized is True
    assert result.kind == "movimento"
    assert result.amount_cents == -1250
    assert result.description == "Esselunga"
    assert result.is_satispay is False


def test_movimento_entrata_is_positive():
    result = _result_from_tool_input(
        {"riconosciuto": True, "tipo": "movimento", "segno": "entrata", "importo_euro": 1200, "descrizione": "Stipendio"}
    )
    assert result.amount_cents == 120000


def test_satispay_flag_is_carried_through():
    result = _result_from_tool_input(
        {
            "riconosciuto": True,
            "tipo": "movimento",
            "segno": "spesa",
            "importo_euro": 5,
            "descrizione": "Bar",
            "e_satispay": True,
        }
    )
    assert result.is_satispay is True


def test_previsione_carries_date():
    result = _result_from_tool_input(
        {
            "riconosciuto": True,
            "tipo": "previsione",
            "segno": "spesa",
            "importo_euro": 150,
            "descrizione": "Rata condominio",
            "data": "2026-10-05",
        }
    )
    assert result.kind == "previsione"
    assert result.date == "2026-10-05"
    assert result.amount_cents == -15000


def test_richiesta_saldo_carries_only_date():
    result = _result_from_tool_input({"riconosciuto": True, "tipo": "richiesta_saldo", "data": "2026-12-01"})
    assert result.kind == "richiesta_saldo"
    assert result.date == "2026-12-01"
    assert result.amount_cents is None


def test_missing_optional_fields_default_sensibly():
    result = _result_from_tool_input({"riconosciuto": True, "tipo": "movimento"})
    assert result.amount_cents is None
    assert result.description is None
    assert result.is_satispay is False
