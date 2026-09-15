from cashmon.bot.entry_parsing import parse_balance_forecast_request, parse_entry, parse_projection


def test_plain_spesa():
    entry = parse_entry("spesa 12,50 Esselunga")
    assert entry.amount_cents == -1250
    assert entry.description == "Esselunga"
    assert entry.source == "telegram"
    assert entry.counts_toward_balance == 1


def test_plain_entrata():
    entry = parse_entry("entrata 1200 Stipendio")
    assert entry.amount_cents == 120000
    assert entry.source == "telegram"
    assert entry.counts_toward_balance == 1


def test_satispay_spesa_does_not_count_toward_balance():
    entry = parse_entry("satispay spesa 12,50 Bar")
    assert entry.amount_cents == -1250
    assert entry.description == "Bar"
    assert entry.source == "satispay"
    assert entry.counts_toward_balance == 0


def test_satispay_entrata_is_positive_and_also_excluded():
    entry = parse_entry("satispay entrata 20 da Mario")
    assert entry.amount_cents == 2000
    assert entry.description == "da Mario"
    assert entry.source == "satispay"
    assert entry.counts_toward_balance == 0


def test_satispay_only_recognized_as_a_leading_prefix_not_in_description():
    # "satispay" appearing inside a normal spesa's description must NOT be
    # mistaken for the special prefix.
    entry = parse_entry("spesa 12,50 satispay ricarica")
    assert entry.amount_cents == -1250
    assert entry.description == "satispay ricarica"
    assert entry.source == "telegram"
    assert entry.counts_toward_balance == 1


def test_case_insensitive():
    entry = parse_entry("SATISPAY SPESA 5 Caffe")
    assert entry.source == "satispay"
    assert entry.amount_cents == -500


def test_unrecognized_text_returns_none():
    assert parse_entry("ciao come stai") is None


def test_unparseable_amount_returns_none():
    # Matches the amount pattern's character class but isn't a valid number.
    assert parse_entry("spesa 12,,50 Esselunga") is None


def test_non_numeric_amount_does_not_match_at_all():
    assert parse_entry("spesa non-un-numero Esselunga") is None


def test_projection_spesa():
    projection = parse_projection("previsione spesa 150 il 2026-10-05 Rata condominio")
    assert projection.date == "2026-10-05"
    assert projection.amount_cents == -15000
    assert projection.description == "Rata condominio"


def test_projection_entrata():
    projection = parse_projection("previsione entrata 1200 il 2026-11-27 Stipendio bonus")
    assert projection.date == "2026-11-27"
    assert projection.amount_cents == 120000
    assert projection.description == "Stipendio bonus"


def test_projection_not_matched_by_plain_entry_parser():
    # "previsione spesa ..." must never be swallowed by the plain spesa/
    # entrata parser -- the two formats are handled by different code paths.
    assert parse_entry("previsione spesa 150 il 2026-10-05 Rata condominio") is None


def test_plain_entry_not_matched_by_projection_parser():
    assert parse_projection("spesa 12,50 Esselunga") is None


def test_projection_requires_valid_looking_date_shape():
    assert parse_projection("previsione spesa 150 il ottobre Rata condominio") is None


def test_balance_forecast_request():
    assert parse_balance_forecast_request("previsione saldo al 2026-12-01") == "2026-12-01"


def test_balance_forecast_request_case_insensitive():
    assert parse_balance_forecast_request("Previsione Saldo Al 2026-12-01") == "2026-12-01"


def test_balance_forecast_request_not_matched_by_projection_parser():
    assert parse_projection("previsione saldo al 2026-12-01") is None


def test_unrelated_text_does_not_match_balance_forecast():
    assert parse_balance_forecast_request("previsione spesa 150 il 2026-10-05 Rata condominio") is None
