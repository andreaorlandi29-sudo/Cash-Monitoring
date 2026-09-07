from cashmon.bot.entry_parsing import parse_entry


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
