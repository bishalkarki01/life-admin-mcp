from datetime import date
from pathlib import Path

import pytest

from life_admin_mcp.statements import normalise_merchant, parse_statement, scan

SAMPLE = (Path(__file__).parent.parent / "examples" / "sample-statement.csv").read_text()
TODAY = date(2026, 11, 25)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("NETFLIX.COM 866-579-7172 CA", "Netflix"),
        ("POS DEBIT SPOTIFY USA P1004", "Spotify"),
        ("PAYPAL *HULU LLC", "Hulu"),
        ("APPLE.COM/BILL 866-712-7753", "Apple"),
        ("DD PLANET FITNESS #1234", "Planet Fitness"),
        ("Google *YouTube Premium", "Youtube Premium"),
        ("SQ *BLUE BOTTLE COFFEE", "Blue Bottle Coffee"),
        ("PG&E WEB ONLINE", "PG&E"),
        ("ADOBE *CREATIVE CLD REF7700", "Adobe"),
        ("7-ELEVEN 3321", "7 Eleven"),
    ],
)
def test_normalise_merchant(raw, expected):
    assert normalise_merchant(raw) == expected


def by_name(result):
    return {r.merchant: r for r in result.recurring}


def test_sample_statement_finds_every_subscription_and_no_noise():
    r = scan(SAMPLE, TODAY)
    found = by_name(r)
    assert set(found) == {
        "Netflix",
        "Spotify",
        "Planet Fitness",
        "Apple",
        "Hulu",
        "Max",
        "Verizon Wireless",
        "PG&E",
        "Adobe",
        "Amazon Prime",
        "Youtube Premium",
        "Hellofresh",
    }
    assert found["Amazon Prime"].cycle == "yearly"
    assert found["Hellofresh"].cycle == "weekly"
    assert found["Planet Fitness"].next_expected == "2026-12-01"


def test_price_rise_variable_bills_and_ended_subscriptions():
    found = by_name(scan(SAMPLE, TODAY))
    nf = found["Netflix"]
    assert nf.price_change == (15.49, 17.99) and nf.price_changed_on == "2026-09-02" and nf.variable is None
    assert found["PG&E"].variable is not None and found["PG&E"].price_change is None
    assert found["Verizon Wireless"].variable is not None
    assert found["Hellofresh"].ended and not nf.ended


def test_debit_credit_columns_eu_dates_and_semicolons():
    csv_text = (
        "Account: 1234\n"
        "Date;Payee;Debit;Credit\n"
        "03.01.2026;NETFLIX.COM;12,99;\n"
        "03.02.2026;NETFLIX.COM;12,99;\n"
        "03.03.2026;NETFLIX.COM;12,99;\n"
        "15.01.2026;SALARY;;3.000,00\n"
        "20.02.2026;BAKERY;4,50;\n"
    )
    r = scan(csv_text, date(2026, 3, 20), default_currency="EUR")
    assert r.date_format == "eu"
    [nf] = r.recurring
    assert (nf.merchant, nf.amount, nf.currency, nf.cycle) == ("Netflix", 12.99, "EUR", "monthly")


def test_positive_spend_convention_and_currency_column():
    csv_text = (
        "Posted Date,Description,Amount,Currency\n"
        "2026-01-05,SPOTIFY,11.99,GBP\n2026-02-05,SPOTIFY,11.99,GBP\n2026-03-05,SPOTIFY,11.99,GBP\n"
        "2026-01-20,REFUND SHOP,-20.00,GBP\n"
    )
    [sp] = scan(csv_text, date(2026, 3, 20)).recurring
    assert sp.currency == "GBP" and sp.amount == 11.99


def test_ambiguous_dates_warn_and_short_statements_warn():
    csv_text = "Date,Description,Amount\n01/02/2026,GYM,-30\n02/02/2026,GYM,-30\n"
    r = scan(csv_text, date(2026, 3, 1))
    assert any("MM/DD/YYYY" in w for w in r.warnings)
    assert any("only" in w and "days" in w for w in r.warnings)


@pytest.mark.parametrize(
    "bad,msg",
    [
        ("", "empty"),
        ("foo,bar\n1,2\n", "header row"),
        ("Date,Amount\n2026-01-01,5\n", "description"),
        ("Date,Description,Amount\nyesterday,X,5\n", "date column format"),
    ],
)
def test_bad_statements_explain_the_problem(bad, msg):
    with pytest.raises(ValueError, match=msg):
        parse_statement(bad)


def test_irregular_shopping_is_not_recurring():
    rows = ["Date,Description,Amount"] + [f"2026-01-{d:02d},SAFEWAY,-{10 + d}.00" for d in (2, 9, 11, 23, 30)]
    assert scan("\n".join(rows), date(2026, 2, 1)).recurring == []
