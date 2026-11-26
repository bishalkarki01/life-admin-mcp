from datetime import date
from pathlib import Path

import pytest

from life_admin_mcp.statements import normalise_merchant, parse_statement, scan

SAMPLE = (Path(__file__).parent.parent / "examples" / "sample-statement.csv").read_text(encoding="utf-8")
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


# --------------------------------------------------------------------------- regressions
def _monthly(rows: list[str], header: str = "Date,Description,Amount") -> str:
    return "\n".join([header, *rows]) + "\n"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("MCAFEE.COM 888-847-8766", "Mcafee"),
        ("MCDONALDS 1234", "Mcdonalds"),
        ("POSTMATES", "Postmates"),
        ("WEBEX", "Webex"),
        ("CARDINAL HEALTH", "Cardinal Health"),
        ("ACHIEVE FITNESS", "Achieve Fitness"),
        ("POS MCAFEE.COM", "Mcafee"),
        ("TST* JOES PIZZA", "Joes Pizza"),
    ],
)
def test_noise_prefixes_only_strip_whole_words(raw, expected):
    assert normalise_merchant(raw) == expected


@pytest.mark.parametrize(
    "text,cents",
    [
        ("$-12.00", -1200),
        ("USD -12.00", -1200),
        ("€-12,00", -1200),
        ("−12.00", -1200),
        ("12.00-", -1200),
        ("(12.00)", -1200),
        ("-$12.00", -1200),
        ("$12.00", 1200),
    ],
)
def test_amount_sign_anywhere(text, cents):
    from life_admin_mcp.statements import _parse_amount

    assert _parse_amount(text) == cents


def test_sign_after_currency_symbol_keeps_income_out():
    rows = []
    for m in range(1, 7):
        rows += [f"2026-{m:02d}-03,NETFLIX.COM,$-15.49", f"2026-{m:02d}-15,ACME PAYROLL,$3000.00"]
    r = scan(_monthly(rows), TODAY)
    assert [x.merchant for x in r.recurring] == ["Netflix"]


def test_value_date_column_is_not_the_amount():
    rows = [f"2026-{m:02d}-05;2026-{m:02d}-06;NETFLIX.COM;15,49;" for m in range(1, 7)]
    rows.append("2026-06-20;2026-06-20;SALARY;;3000,00")
    text = _monthly(rows, "Booking Date;Value Date;Description;Debit;Credit")
    txns, meta = parse_statement(text)
    assert meta["skipped"] == 0
    assert {t.amount_cents for t in txns} == {1549}
    [r] = scan(text, TODAY).recurring
    assert (r.merchant, r.amount) == ("Netflix", 15.49)


def test_debit_amount_and_credit_amount_columns():
    rows = [f"2026-{m:02d}-05,NETFLIX.COM,15.49," for m in range(1, 7)] + ["2026-06-20,SALARY,,3000.00"]
    text = _monthly(rows, "Date,Description,Debit Amount,Credit Amount")
    txns, meta = parse_statement(text)
    assert meta["skipped"] == 0 and len(txns) == 6


def test_same_merchant_two_subscriptions_same_day():
    rows = []
    for m in range(1, 7):
        rows += [f"2026-{m:02d}-01,APPLE.COM/BILL,-2.99", f"2026-{m:02d}-01,APPLE.COM/BILL,-10.99"]
    found = sorted(r.amount for r in scan(_monthly(rows), TODAY).recurring)
    assert found == [2.99, 10.99]


def test_month_end_charge_keeps_month_end_anchor():
    rows = ["2026-01-31,GYM,-40.00", "2026-02-28,GYM,-40.00"]
    [r] = scan(_monthly(rows), date(2026, 3, 1)).recurring
    assert r.next_expected == "2026-03-31"


def test_header_only_statement_says_no_transactions():
    with pytest.raises(ValueError, match="no transactions"):
        parse_statement("Date,Description,Amount\n")


def test_one_day_late_posting_does_not_shift_the_anchor():
    rows = [
        f"{d},PLANET FITNESS,-24.99" for d in ("2025-10-28", "2025-11-28", "2025-12-29", "2026-01-28", "2026-02-28")
    ]
    [r] = scan(_monthly(rows), date(2026, 3, 2)).recurring
    assert r.next_expected == "2026-03-28"


def test_same_day_merchant_price_change_is_one_subscription():
    rows = []
    for m in range(1, 11):
        rows += [
            f"2026-{m:02d}-15,APPLE.COM/BILL,-10.99",
            f"2026-{m:02d}-15,APPLE.COM/BILL,-{2.99 if m <= 5 else 3.99}",
        ]
    found = sorted((r.amount, r.price_change, r.ended) for r in scan(_monthly(rows), TODAY).recurring)
    assert found == [(3.99, (2.99, 3.99), False), (10.99, None, False)]
