"""Find recurring charges in a bank or card statement CSV.

Everything runs locally and nothing from the statement is stored. The detector:

1. parses CSVs from most banks (it auto-detects the delimiter, the column names,
   whether spending is positive or negative, and the date format),
2. normalises merchant names ("NETFLIX.COM 866-579-7172 CA" -> "Netflix"),
3. groups charges by merchant and looks for a regular cadence
   (weekly, monthly, quarterly, yearly), and
4. scores each candidate and predicts its next charge date.
"""

from __future__ import annotations

import calendar
import csv
import io
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime

from .dates import advance

MAX_BYTES = 5_000_000
MAX_ROWS = 50_000

DATE_COLS = ("transaction date", "trans date", "date", "posted date", "posting date", "booking date", "value date")
DESC_COLS = ("merchant", "payee", "description", "name", "details", "transaction description", "memo", "narrative")
AMOUNT_COLS = ("amount", "transaction amount", "value")
DEBIT_COLS = ("debit", "withdrawal", "withdrawals", "money out", "paid out", "debit amount")
CREDIT_COLS = ("credit", "deposit", "deposits", "money in", "paid in", "credit amount")
CURRENCY_COLS = ("currency", "ccy")
# A header containing one of these is never the single amount column ("Value Date", "Debit Amount", "Balance").
_NOT_AMOUNT = ("date", "balance") + DEBIT_COLS + CREDIT_COLS

# Cycle name -> (typical days, min days, max days)
CADENCES = {
    "weekly": (7, 6, 8),
    "monthly": (30, 26, 35),
    "quarterly": (91, 84, 98),
    "yearly": (365, 350, 380),
}

# Word prefixes only count when followed by a separator, so "MCDONALDS" and "POSTMATES" survive.
_NOISE = re.compile(
    r"^(?:(?:POS|DEBIT|CREDIT|CARD|PURCHASE|RECURRING|PAYMENT|ACH|DD|DIRECT DEBIT|VISA|MC|CHECKCARD|PREAUTHORIZED|"
    r"ONLINE|WEB|AUTOPAY|BILL PAY)(?:\s*[-:#/]\s*|\s+)|(?:SQ|TST|PAYPAL|PP|GOOGLE) ?\*\s*)",
    re.I,
)
_MINUS = str.maketrans({"\u2212": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-"})
_TAIL = re.compile(
    r"(\s+[A-Z]{2}\s*$)|(\s+\d[\d\-\s#*/.]*.*$)|(\s+(REF|ID|TXN|AUTH|CARD)\b.*$)|(\*[\w\d]+.*$)|(\s+X{2,}\d*.*$)",
    re.I,
)
_DOMAIN = re.compile(r"\.(COM|NET|ORG|IO|CO|TV|APP)\b.*$", re.I)


@dataclass
class Txn:
    day: date
    raw: str
    merchant: str
    amount_cents: int
    currency: str


@dataclass
class Recurring:
    merchant: str
    cycle: str
    amount: float
    currency: str
    occurrences: int
    first_seen: str
    last_seen: str
    next_expected: str
    confidence: str  # high | medium
    price_change: tuple[float, float] | None = None  # (previous, latest)
    price_changed_on: str | None = None
    variable: tuple[float, float] | None = None  # (min, max) for bills whose amount varies
    ended: bool = False  # no charge for well over one cycle before the statement ends
    examples: list[str] = field(default_factory=list)

    @property
    def annual_cost(self) -> float:
        per_year = {"weekly": 52, "monthly": 12, "quarterly": 4, "yearly": 1}[self.cycle]
        return round(self.amount * per_year, 2)


@dataclass
class ScanResult:
    recurring: list[Recurring]
    rows_read: int
    charges_read: int
    skipped_rows: int
    date_range: tuple[str, str] | None
    date_format: str
    warnings: list[str]


# --------------------------------------------------------------------------- parsing helpers
def normalise_merchant(raw: str) -> str:
    """Turn a raw statement description into a stable, readable merchant name."""
    s = " ".join(raw.upper().split())
    for _ in range(3):  # strip stacked prefixes like "POS DEBIT SQ *"
        s2 = _NOISE.sub("", s)
        if s2 == s:
            break
        s = s2
    s = _DOMAIN.sub("", s)
    s = _TAIL.sub("", s)
    s = re.sub(r"[^A-Z0-9&+' ]", " ", s)
    suffixes = {
        "LLC",
        "INC",
        "LTD",
        "CORP",
        "CO",
        "USA",
        "US",
        "GMBH",
        "PLC",
        "BILL",
        "PAYMENT",
        "SUBSCRIPTION",
        "WEB",
        "ONLINE",
        "AUTOPAY",
        "RECURRING",
    }
    parts = s.split()
    words = parts[:1] + [w for w in parts[1:] if not any(ch.isdigit() for ch in w)]
    while len(words) > 1 and words[-1] in suffixes:
        words.pop()
    words = words[:3]
    name = " ".join(words).strip()
    if not name:
        return raw.strip()[:40] or "Unknown"
    keep = {"AT&T", "BBC", "HBO", "AWS", "ATT", "TV", "UK", "US", "XM"}
    return " ".join(w if w in keep or "&" in w else w.capitalize() for w in name.split())


def _parse_amount(text: str) -> int | None:
    t = (text or "").strip()
    if not t:
        return None
    t = t.translate(_MINUS)
    # The sign can sit anywhere: "-12.00", "12.00-", "$-12.00", "USD -12.00", "(12.00)".
    neg = "-" in t or (t.startswith("(") and t.endswith(")"))
    t = re.sub(r"[^\d.,]", "", t)
    if not t:
        return None
    # "1.234,56" (European) vs "1,234.56"
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".") else t.replace(",", "")
    elif "," in t:
        head, _, tail = t.rpartition(",")
        t = f"{head.replace(',', '')}.{tail}" if len(tail) == 2 else t.replace(",", "")
    try:
        cents = round(float(t) * 100)
    except ValueError:
        return None
    return -cents if neg else cents


_DATE_FORMATS = {
    "iso": ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d"),
    "us": ("%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m-%d-%y"),
    "eu": ("%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d.%m.%Y", "%d.%m.%y"),
    "text": ("%b %d, %Y", "%d %b %Y", "%B %d, %Y", "%d %B %Y", "%b %d %Y", "%d-%b-%Y", "%d-%b-%y"),
}


def _try_formats(value: str, fmts: tuple[str, ...]) -> date | None:
    v = value.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}[T ]", v):  # ISO timestamp: keep the date part
        v = v[:10]
    for f in fmts:
        try:
            return datetime.strptime(v, f).date()
        except ValueError:
            continue
    return None


def _choose_date_format(samples: list[str], requested: str) -> tuple[str, bool]:
    """Return (format, ambiguous). Ambiguous means US and EU readings both fit every sample."""
    if requested != "auto":
        if requested not in _DATE_FORMATS:
            raise ValueError(f"date_format must be auto, {', '.join(_DATE_FORMATS)} (got {requested!r})")
        return requested, False
    scores = {}
    for name, fmts in _DATE_FORMATS.items():
        scores[name] = sum(1 for s in samples if _try_formats(s, fmts))
    # US vs EU tie (all days <= 12): prefer US, but say so in a warning
    best = max(scores, key=lambda k: (scores[k], k == "us", k == "iso"))
    if scores[best] == 0:
        raise ValueError(
            "Couldn't recognise the date column format. Pass date_format as iso, us (MM/DD/YYYY), "
            "eu (DD/MM/YYYY) or text (e.g. 'Nov 3, 2026')."
        )
    return best, best in ("us", "eu") and scores["us"] == scores["eu"]


def _find(headers: list[str], names: tuple[str, ...], exclude: tuple[str, ...] = ()) -> int | None:
    lowered = [h.strip().lower() for h in headers]
    for n in names:
        if n in lowered:
            return lowered.index(n)
    for n in names:  # looser: header contains the word
        for i, h in enumerate(lowered):
            if n in h and not any(x in h for x in exclude):
                return i
    return None


def _find_amount(headers: list[str]) -> int | None:
    return _find(headers, AMOUNT_COLS, exclude=_NOT_AMOUNT)


# --------------------------------------------------------------------------- parse
def parse_statement(text: str, date_format: str = "auto", default_currency: str = "USD") -> tuple[list[Txn], dict]:
    if len(text.encode("utf-8", "ignore")) > MAX_BYTES:
        raise ValueError(f"Statement is larger than {MAX_BYTES // 1_000_000} MB; export a shorter date range.")
    text = text.lstrip("﻿").strip()
    if not text:
        raise ValueError("The statement is empty.")
    # Banks often put account details above the header, which confuses csv.Sniffer.
    # Try the sniffed delimiter first, then the common ones, and keep the first that yields a header row.
    candidates: list[str] = []
    try:
        candidates.append(csv.Sniffer().sniff(text[:4096], delimiters=",;\t|").delimiter)
    except csv.Error:
        pass
    candidates += [d for d in (",", ";", "\t", "|") if d not in candidates]
    rows: list[list[str]] = []
    header_idx = None
    for delim in candidates:
        rows = list(csv.reader(io.StringIO(text), delimiter=delim))
        for i, row in enumerate(rows[:30]):
            if (
                len(row) >= 2
                and _find(row, DATE_COLS) is not None
                and (_find_amount(row) is not None or _find(row, DEBIT_COLS) is not None)
            ):
                header_idx = i
                break
        if header_idx is not None:
            break
    if header_idx is None:
        raise ValueError(
            "Couldn't find a header row with a date column and an amount (or debit) column. "
            "Expected headers like: Date, Description, Amount."
        )
    headers, data = rows[header_idx], rows[header_idx + 1 :]
    if len(data) > MAX_ROWS:
        raise ValueError(f"Statement has more than {MAX_ROWS:,} rows; export a shorter date range.")
    c_date = _find(headers, DATE_COLS)
    c_desc = _find(headers, DESC_COLS)
    c_amt = _find_amount(headers)
    c_debit = _find(headers, DEBIT_COLS)
    c_credit = _find(headers, CREDIT_COLS)
    c_cur = _find(headers, CURRENCY_COLS)
    if c_desc is None:
        raise ValueError("Couldn't find a description/merchant/payee column.")
    assert c_date is not None  # noqa: S101 - guaranteed by header detection above

    samples = [r[c_date] for r in data[:200] if len(r) > c_date and r[c_date].strip()]
    if not samples:
        raise ValueError("The statement has a header row but no transactions. Export a date range with charges.")
    fmt, ambiguous = _choose_date_format(samples, date_format)
    raw_rows: list[tuple[date, str, int, str]] = []
    skipped = 0
    for r in data:
        try:
            d = _try_formats(r[c_date], _DATE_FORMATS[fmt])
            desc = r[c_desc].strip()
            cur = (
                r[c_cur].strip().upper()
                if c_cur is not None and len(r) > c_cur and r[c_cur].strip()
                else default_currency
            )
            if c_debit is not None and c_amt is None:
                debit = _parse_amount(r[c_debit]) if len(r) > c_debit else None
                credit = _parse_amount(r[c_credit]) if c_credit is not None and len(r) > c_credit else None
                amt = -abs(debit) if debit else (abs(credit) if credit else None)
            else:
                assert c_amt is not None  # noqa: S101
                amt = _parse_amount(r[c_amt])
        except IndexError:
            skipped += 1
            continue
        if d is None or amt is None or not desc:
            skipped += 1
            continue
        raw_rows.append((d, desc, amt, cur))

    # Which sign means "money out"? With a single Amount column most banks use negative,
    # some card issuers use positive. Decide by majority.
    negatives = sum(1 for _, _, a, _ in raw_rows if a < 0)
    spend_is_negative = c_amt is None or negatives >= len(raw_rows) / 2
    txns = [
        Txn(day=d, raw=desc, merchant=normalise_merchant(desc), amount_cents=abs(a), currency=cur)
        for d, desc, a, cur in raw_rows
        if (a < 0) == spend_is_negative and a != 0
    ]
    meta = {
        "rows": len(data),
        "skipped": skipped,
        "date_format": fmt,
        "ambiguous_dates": ambiguous,
        "spend_sign": "negative" if spend_is_negative else "positive",
    }
    return txns, meta


# --------------------------------------------------------------------------- detect
def _cadence(intervals: list[int]) -> tuple[str, float] | None:
    """Return (cycle, regularity 0..1) if intervals fit a known cadence."""
    if not intervals:
        return None
    med = statistics.median(intervals)
    for name, (_, lo, hi) in CADENCES.items():
        if lo <= med <= hi:
            fit = sum(1 for i in intervals if lo <= i <= hi) / len(intervals)
            return name, fit
    return None


def _split_by_amount(items: list[Txn]) -> list[list[Txn]]:
    """Split one merchant's charges into series of similar amounts (iCloud and Apple Music both bill as Apple)."""
    series: list[list[Txn]] = []
    for t in items:
        for sr in series:
            first, last = sr[0], sr[-1]
            if last.day != t.day and abs(t.amount_cents - first.amount_cents) <= max(first.amount_cents, 1) * 0.01:
                sr.append(t)
                break
        else:
            series.append([t])
    # Re-join a series that stops just as another starts on the same cadence: that is one
    # subscription whose price changed (iCloud going from 2.99 to 3.99), not two.
    series.sort(key=lambda sr: sr[0].day)
    merged: list[list[Txn]] = []
    for sr in series:
        for m in merged:
            gap = (sr[0].day - m[-1].day).days
            own = _cadence([(b.day - a.day).days for a, b in zip(m, m[1:], strict=False)])
            follow = _cadence([gap])
            if gap > 0 and own and follow and own[0] == follow[0] and len(m) >= 2:
                m.extend(sr)
                break
        else:
            merged.append(sr)
    return merged


def detect_recurring(txns: list[Txn], today: date, statement_end: date | None = None) -> list[Recurring]:
    statement_end = statement_end or max((t.day for t in txns), default=today)
    groups: dict[tuple[str, str], list[Txn]] = defaultdict(list)
    for t in txns:
        groups[(t.merchant.lower(), t.currency)].append(t)
    found: list[Recurring] = []
    for (_, currency), items in groups.items():
        items.sort(key=lambda t: t.day)
        # Collapse same-day duplicates (e.g. pending + posted)
        uniq: list[Txn] = []
        for t in items:
            if uniq and uniq[-1].day == t.day and uniq[-1].amount_cents == t.amount_cents:
                continue
            uniq.append(t)
        # Different amounts on the same day mean several subscriptions from one merchant.
        if not any(a.day == b.day for a, b in zip(uniq, uniq[1:], strict=False)):
            r = _analyse(uniq, currency, today, statement_end)
            if r is not None:
                found.append(r)
            continue
        # Each series must be a steady price, and together they must explain most of the merchant's
        # charges; otherwise this is ordinary shopping that sometimes happens twice a day.
        parts = [_analyse(sr, currency, today, statement_end) for sr in _split_by_amount(uniq)]
        steady = [r for r in parts if r is not None and r.variable is None and r.confidence == "high"]
        if sum(r.occurrences for r in steady) >= 0.8 * len(uniq):
            found.extend(steady)
    found.sort(key=lambda r: r.annual_cost, reverse=True)
    return found


def _analyse(uniq: list[Txn], currency: str, today: date, statement_end: date) -> Recurring | None:
    if len(uniq) < 2:
        return None
    intervals = [(b.day - a.day).days for a, b in zip(uniq, uniq[1:], strict=False)]
    if any(i == 0 for i in intervals):
        return None
    cad = _cadence(intervals)
    if not cad:
        return None
    cycle, regularity = cad
    need = 3 if cycle == "weekly" else 2
    if len(uniq) < need or regularity < 0.6:
        return None
    amounts = [t.amount_cents for t in uniq]
    latest = amounts[-1]
    # Variable bills (utilities) still recur; very erratic amounts are probably ordinary shopping.
    spread = max(amounts) / max(min(amounts), 1)
    if spread > 3:
        return None
    price_change = None
    price_changed_on = None
    variable = None
    # Split the amounts into runs of (nearly) equal prices. A subscription whose price rose
    # looks like a few long runs; a utility bill changes every time.
    runs: list[list[int]] = []  # [amount, length, start index]
    for idx, a in enumerate(amounts):
        if runs and abs(a - runs[-1][0]) <= max(runs[-1][0], 1) * 0.01:
            runs[-1][1] += 1
        else:
            runs.append([a, 1, idx])
    stepwise = len(runs) <= 3 and all(r[1] >= 2 for r in runs[:-1])
    if not stepwise:
        variable = (min(amounts) / 100, max(amounts) / 100)
        latest = round(statistics.median(amounts[-3:]))
    elif len(runs) > 1:
        price_change = (runs[-2][0] / 100, runs[-1][0] / 100)
        price_changed_on = uniq[runs[-1][2]].day.isoformat()
    typical_days = CADENCES[cycle][0]
    ended = (statement_end - uniq[-1].day).days > typical_days * 1.5 + 5
    confidence = "high" if len(uniq) >= 3 and regularity >= 0.8 and variable is None else "medium"
    nxt = uniq[-1].day
    anchor = nxt.day
    usual = Counter(t.day.day for t in uniq).most_common(1)[0][0]
    if cycle != "weekly" and calendar.monthrange(nxt.year, nxt.month)[1] < usual:
        # A charge on Feb 28 that is usually on the 31st was clamped to February; keep the 31st.
        anchor = usual
    for _ in range(60):
        nxt = advance(nxt, cycle, anchor)
        if nxt >= today:
            break
    return Recurring(
        merchant=uniq[-1].merchant,
        cycle=cycle,
        amount=latest / 100,
        currency=currency,
        occurrences=len(uniq),
        first_seen=uniq[0].day.isoformat(),
        last_seen=uniq[-1].day.isoformat(),
        next_expected=nxt.isoformat(),
        confidence=confidence,
        price_change=price_change,
        price_changed_on=price_changed_on,
        variable=variable,
        ended=ended,
        examples=list(dict.fromkeys(t.raw for t in uniq))[:2],
    )


def scan(text: str, today: date, date_format: str = "auto", default_currency: str = "USD") -> ScanResult:
    txns, meta = parse_statement(text, date_format, default_currency)
    warnings = []
    days = [t.day for t in txns]
    if meta["skipped"]:
        warnings.append(f"Skipped {meta['skipped']} row(s) without a readable date, amount or description.")
    if txns:
        span = (max(t.day for t in txns) - min(t.day for t in txns)).days
        if span < 60:
            warnings.append(
                f"The statement covers only {span} days. Export at least 3 months to catch monthly charges "
                "reliably, or 13 months to catch yearly ones."
            )
    if meta["ambiguous_dates"]:
        warnings.append(
            "Dates were read as MM/DD/YYYY. If your bank uses DD/MM/YYYY, scan again with date_format='eu'."
        )
    return ScanResult(
        recurring=detect_recurring(txns, today, max(days) if days else None),
        rows_read=meta["rows"],
        charges_read=len(txns),
        skipped_rows=meta["skipped"],
        date_range=(min(days).isoformat(), max(days).isoformat()) if days else None,
        date_format=meta["date_format"],
        warnings=warnings,
    )
