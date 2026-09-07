from cashmon.bot.formatting import format_eur, parse_amount_to_cents


def test_format_eur_positive():
    assert format_eur(1_000_000) == "10.000,00 €"


def test_format_eur_negative():
    assert format_eur(-4530) == "-45,30 €"


def test_parse_amount_with_comma_decimal():
    assert parse_amount_to_cents("12,50") == 1250


def test_parse_amount_with_dot_decimal():
    assert parse_amount_to_cents("12.50") == 1250


def test_parse_amount_integer():
    assert parse_amount_to_cents("1200") == 120000


def test_parse_amount_thousands_and_decimal_comma():
    assert parse_amount_to_cents("1.200,50") == 120050
