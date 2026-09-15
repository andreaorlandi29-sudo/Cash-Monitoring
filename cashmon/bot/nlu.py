"""Conversational fallback for the Telegram bot: when a message doesn't match
any of the rigid formats in entry_parsing.py, this asks Claude to extract the
same structured intent from freer Italian phrasing -- "ho speso 12 euro da
Esselunga", "domani mi arrivano 1200 di stipendio", "quanto avrò a dicembre?".

Kept separate from telegram_bot.py, and split into a network call
(`interpret`) and a pure parsing function (`_result_from_tool_input`), so the
extraction logic is unit-testable without hitting the API (mirrors
entry_parsing.py's separation from the python-telegram-bot dependency).

This is strictly a fallback: the free, deterministic rigid parsers in
entry_parsing.py are always tried first (see handle_text in telegram_bot.py)
and never reach this module. It's also optional -- with no ANTHROPIC_API_KEY
configured, telegram_bot.py skips it entirely and the bot works exactly as
before, just without free-form phrasing.
"""
from dataclasses import dataclass
from typing import Optional

import anthropic

from cashmon import config as app_config

INTENT_TOOL = {
    "name": "interpreta_messaggio",
    "description": (
        "Estrae l'intento strutturato da un messaggio Telegram in italiano su un "
        "movimento finanziario personale: una spesa/entrata reale, una previsione futura, "
        "oppure una richiesta di sapere il saldo previsto a una data. "
        "Va chiamato sempre, anche quando il messaggio non c'entra nulla o manca di "
        "dettagli essenziali -- in quel caso imposta riconosciuto=false e spiega perché."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "riconosciuto": {
                "type": "boolean",
                "description": (
                    "true solo se il messaggio descrive chiaramente un movimento di denaro "
                    "personale (spesa/entrata, con importo e segno chiari) oppure una richiesta "
                    "di saldo previsto con una data risolvibile. false per messaggi generici, "
                    "saluti, domande non pertinenti, o quando manca l'importo/il segno/la data "
                    "necessari."
                ),
            },
            "motivo_non_chiaro": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "description": (
                    "Se riconosciuto=false: breve spiegazione in italiano di cosa manca "
                    "(es. 'importo non specificato', 'non chiaro se spesa o entrata'). "
                    "null se riconosciuto=true."
                ),
            },
            "tipo": {
                "type": "string",
                "enum": ["movimento", "previsione", "richiesta_saldo", "non_pertinente"],
                "description": (
                    "'movimento': spesa o entrata reale e immediata (oggi, salvo data diversa esplicita). "
                    "'previsione': spesa o entrata futura pianificata per una data precisa. "
                    "'richiesta_saldo': l'utente chiede quale sarà il saldo previsto a una certa data. "
                    "'non_pertinente': il messaggio non riguarda nessuno di questi casi."
                ),
            },
            "e_satispay": {
                "type": "boolean",
                "description": (
                    "true solo se il messaggio menziona esplicitamente Satispay, false altrimenti. "
                    "Campo obbligatorio: valuta sempre esplicitamente se Satispay è menzionato o no."
                ),
            },
            "segno": {
                "anyOf": [{"type": "string", "enum": ["spesa", "entrata"]}, {"type": "null"}],
                "description": (
                    "obbligatorio (non null) per tipo=movimento o tipo=previsione: indica se è una "
                    "spesa o un'entrata. null per tipo=richiesta_saldo o tipo=non_pertinente."
                ),
            },
            "importo_euro": {
                "anyOf": [{"type": "number"}, {"type": "null"}],
                "description": (
                    "obbligatorio (non null) per tipo=movimento o tipo=previsione: importo assoluto "
                    "in euro (sempre positivo) menzionato nel messaggio. Estrailo sempre quando è "
                    "presente nel testo, anche se il messaggio è discorsivo. null per "
                    "tipo=richiesta_saldo o tipo=non_pertinente."
                ),
            },
            "data": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "description": (
                    "data in formato YYYY-MM-DD. Risolvi le espressioni relative (oggi, domani, "
                    "lunedì prossimo, fra 3 mesi, ecc.) usando la data odierna indicata nel prompt. "
                    "Per tipo=movimento senza data esplicita, usa la data odierna. Obbligatoria (non "
                    "null) per tipo=previsione e tipo=richiesta_saldo. null per tipo=non_pertinente."
                ),
            },
            "descrizione": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "description": (
                    "breve descrizione/esercente ripulita dal linguaggio discorsivo, es. da "
                    "'ho speso 12 euro da esselunga stamattina' estrai 'Esselunga'. "
                    "Obbligatoria (non null) per tipo=movimento o tipo=previsione. null altrimenti."
                ),
            },
        },
        "required": [
            "riconosciuto",
            "motivo_non_chiaro",
            "tipo",
            "e_satispay",
            "segno",
            "importo_euro",
            "data",
            "descrizione",
        ],
        "additionalProperties": False,
    },
    "strict": True,
}


@dataclass
class NluResult:
    recognized: bool
    kind: str  # 'movimento' | 'previsione' | 'richiesta_saldo' | 'non_pertinente'
    is_satispay: bool = False
    amount_cents: Optional[int] = None  # signed: negative for spesa, positive for entrata
    date: Optional[str] = None  # ISO 8601 YYYY-MM-DD
    description: Optional[str] = None
    reason_unclear: Optional[str] = None


def _result_from_tool_input(data: dict) -> NluResult:
    """Pure conversion from the tool's raw JSON input to NluResult -- no
    network access, so this is what the unit tests exercise directly."""
    if not data.get("riconosciuto"):
        return NluResult(
            recognized=False,
            kind=data.get("tipo", "non_pertinente"),
            reason_unclear=data.get("motivo_non_chiaro"),
        )

    amount_cents = None
    if data.get("importo_euro") is not None:
        amount_cents = round(float(data["importo_euro"]) * 100)
        if data.get("segno") == "spesa":
            amount_cents = -amount_cents

    return NluResult(
        recognized=True,
        kind=data["tipo"],
        is_satispay=bool(data.get("e_satispay")),
        amount_cents=amount_cents,
        date=data.get("data"),
        description=data.get("descrizione"),
    )


def interpret(text: str, today: str) -> NluResult:
    """Calls the Claude API once to extract structured intent from `text`.
    Raises on network/API errors -- the caller (telegram_bot.py) decides how
    to degrade (falls back to the rigid-format help message)."""
    client = anthropic.Anthropic(api_key=app_config.ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=app_config.NLU_MODEL,
        max_tokens=1024,
        tools=[INTENT_TOOL],
        tool_choice={"type": "tool", "name": "interpreta_messaggio"},
        system=(
            "Interpreti messaggi Telegram in italiano per un'app personale di gestione "
            f"delle finanze. Oggi è {today}. Chiama sempre interpreta_messaggio con i "
            "campi che riesci a estrarre con sicurezza."
        ),
        messages=[{"role": "user", "content": text}],
    )
    tool_use = next(block for block in response.content if block.type == "tool_use")
    return _result_from_tool_input(tool_use.input)
