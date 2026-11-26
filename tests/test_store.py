import json
import sqlite3
from datetime import date

import pytest

from life_admin_mcp.store import SCHEMA_VERSION, NotFoundError, Store


def add_netflix(store, **kw):
    args = dict(
        name="Netflix", kind="subscription", due_date="2026-01-20", amount=15.49, cycle="monthly", category="Streaming"
    )
    args.update(kw)
    return store.add(**args)


def test_add_normalises_and_stores_cents(store):
    item = add_netflix(store, name="  Netflix   Premium ", currency="usd")
    assert item.name == "Netflix Premium"
    assert item.category == "streaming"
    assert item.currency == "USD"
    assert item.amount == 15.49
    assert item.annual_cost == 185.88


@pytest.mark.parametrize(
    "field,value,msg",
    [
        ("name", "", "name is required"),
        ("name", "x" * 201, "at most 200"),
        ("kind", "loan", "kind must be one of"),
        ("cycle", "daily", "cycle must be one of"),
        ("due_date", "next week", "YYYY-MM-DD"),
        ("amount", -1, "between 0"),
        ("amount", float("nan"), "between 0"),
        ("amount", "abc", "must be a number"),
        ("currency", "DOLLARS", "ISO 4217"),
        ("remind_days", -1, "between 0 and 3650"),
        ("remind_days", True, "whole number"),
    ],
)
def test_add_validation(store, field, value, msg):
    with pytest.raises(ValueError, match=msg):
        add_netflix(store, **{field: value})


def test_duplicate_active_rejected_but_allowed_after_cancel(store):
    first = add_netflix(store)
    with pytest.raises(ValueError, match="already exists"):
        add_netflix(store, name="netflix")
    store.set_active(first.id, False)
    assert add_netflix(store).id != first.id


def test_missing_ids_raise(store):
    for fn in (
        lambda: store.delete(99),
        lambda: store.update(99, name="x"),
        lambda: store.set_active(99, False),
        lambda: store.mark_used(99),
    ):
        with pytest.raises(NotFoundError):
            fn()


def test_update(store):
    item = add_netflix(store)
    updated = store.update(item.id, amount=17.99, due_date="2026-02-28")
    assert updated.amount == 17.99 and updated.due_date == "2026-02-28"
    with pytest.raises(ValueError, match="Nothing to update"):
        store.update(item.id)
    with pytest.raises(ValueError, match="Unknown field"):
        store.update(item.id, active=False)


def test_recurring_items_roll_forward_and_one_offs_expire(store, clock):
    add_netflix(store, due_date="2025-11-03")
    store.add(name="Passport", kind="document", due_date="2026-01-01")
    items = {i.name: i for i in store.list_items()}
    assert items["Netflix"].due_date == "2026-02-03"
    assert items["Passport"].due_date == "2026-01-01"
    overdue, _ = store.upcoming(30)
    assert [i.name for i in overdue] == ["Passport"]


def test_upcoming_respects_reminder_window(store):
    store.add(name="Passport", kind="document", due_date="2026-06-01", remind_days=180)
    store.add(name="Licence", kind="document", due_date="2026-06-01", remind_days=7)
    _, soon = store.upcoming(30)
    assert [i.name for i in soon] == ["Passport"]
    with pytest.raises(ValueError):
        store.upcoming(0)


def test_spending_by_currency(store):
    add_netflix(store)
    store.add(name="Gym", kind="subscription", due_date="2026-02-01", amount=40, cycle="monthly")
    store.add(name="Spotify", kind="subscription", due_date="2026-02-01", amount=10, currency="EUR", cycle="monthly")
    store.add(name="Laptop", kind="warranty", due_date="2027-01-01")
    s = store.spending()
    assert s["per_currency"]["USD"] == {"monthly": 55.49, "yearly": 665.88}
    assert s["per_currency"]["EUR"]["yearly"] == 120
    assert s["top"][0].name == "Gym"


def test_savings(store, clock):
    a = add_netflix(store)
    store.add(
        name="Hulu", kind="subscription", due_date="2026-02-01", amount=9.99, cycle="monthly", category="streaming"
    )
    store.mark_used(a.id, "2026-01-10")
    s = store.savings(30)
    assert [i.name for i in s["stale"]] == ["Hulu"]
    assert set(i.name for i in s["overlaps"]["streaming"]) == {"Netflix", "Hulu"}
    assert s["potential_yearly"] == {"USD": 119.88}
    with pytest.raises(ValueError, match="future"):
        store.mark_used(a.id, "2030-01-01")


def test_mark_used_only_for_subscriptions(store):
    w = store.add(name="Fridge", kind="warranty", due_date="2027-01-01")
    with pytest.raises(ValueError, match="only subscriptions"):
        store.mark_used(w.id)


def test_calendar_expands_recurrences(store):
    add_netflix(store, due_date="2026-01-31")
    store.add(name="Passport", kind="document", due_date="2026-03-01", remind_days=90, notes="Renew online")
    cal = store.calendar(days=90)
    assert cal.count("BEGIN:VEVENT") == 4  # Netflix Jan 31, Feb 28, Mar 31 + Passport
    assert "DTSTART;VALUE=DATE:20260228" in cal and "DTSTART;VALUE=DATE:20260331" in cal
    assert "TRIGGER:-P90D" in cal and "DESCRIPTION:Renew online" in cal


def test_export_import_roundtrip(tmp_path, store, clock):
    a = add_netflix(store)
    store.mark_used(a.id, "2026-01-10")
    b = store.add(name="Old gym", kind="subscription", due_date="2026-02-01", amount=30, cycle="monthly")
    store.set_active(b.id, False)
    dump = store.export_json()

    fresh = Store(tmp_path / "other.db", today=clock)
    added, errors = fresh.import_json(dump)
    assert (added, errors) == (2, [])
    items = {i.name: i for i in fresh.list_items(include_inactive=True)}
    assert items["Netflix"].last_used == "2026-01-10"
    assert items["Old gym"].active is False
    # Importing again skips the active duplicate
    added, errors = fresh.import_json(dump)
    assert added == 1 and "already exists" in errors[0]


@pytest.mark.parametrize(
    "bad", ["not json", "[]", json.dumps({"format": "other"}), json.dumps({"format": "life-admin-mcp", "items": 5})]
)
def test_import_rejects_bad_payload(store, bad):
    with pytest.raises(ValueError):
        store.import_json(bad)


def test_import_reports_bad_items(store):
    payload = json.dumps(
        {
            "format": "life-admin-mcp",
            "items": [
                {"name": "OK", "kind": "bill", "due_date": "2026-02-01"},
                {"name": "Bad", "kind": "bill", "due_date": "soon"},
                {"kind": "bill"},
                "junk",
            ],
        }
    )
    added, errors = store.import_json(payload)
    assert added == 1 and len(errors) == 3


def test_migrates_prototype_database(tmp_path, clock):
    path = tmp_path / "legacy.db"
    c = sqlite3.connect(path)
    c.execute("""CREATE TABLE items (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, kind TEXT NOT NULL,
        category TEXT DEFAULT '', amount REAL DEFAULT 0, currency TEXT DEFAULT 'USD', cycle TEXT DEFAULT 'once',
        due_date TEXT NOT NULL, remind_days INTEGER DEFAULT 7, last_used TEXT, notes TEXT DEFAULT '',
        active INTEGER DEFAULT 1, created TEXT DEFAULT (date('now')))""")
    c.execute(
        "INSERT INTO items (name, kind, amount, cycle, due_date) "
        "VALUES ('Gym', 'subscription', 39.99, 'monthly', '2026-01-31')"
    )
    c.commit()
    c.close()
    s = Store(path, today=clock)
    [gym] = s.list_items()
    assert gym.amount == 39.99 and gym.due_date == "2026-01-31"
    assert sqlite3.connect(path).execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    Store(path, today=clock)  # re-opening is a no-op


def test_refuses_newer_schema(tmp_path):
    path = tmp_path / "future.db"
    c = sqlite3.connect(path)
    c.execute("PRAGMA user_version=99")
    c.close()
    with pytest.raises(RuntimeError, match="newer version"):
        Store(path)


def test_data_persists_across_instances(tmp_path, clock):
    Store(tmp_path / "p.db", today=clock).add(
        name="Rent", kind="bill", due_date="2026-02-01", amount=1200, cycle="monthly"
    )
    assert Store(tmp_path / "p.db", today=date.today).list_items()[0].name == "Rent"


# --------------------------------------------------------------------------- v2 features
def test_trials_do_not_roll_forward_or_count_as_spending(store, clock):
    t = store.add(name="Duolingo", kind="trial", due_date="2026-01-20", amount=12.99, cycle="monthly")
    assert store.spending()["per_currency"] == {}
    assert [i.name for i in store.spending()["trials"]] == ["Duolingo"]
    assert [i.name for i in store.savings(30)["trials"]] == ["Duolingo"]
    clock.d = date(2026, 2, 1)
    overdue, _ = store.upcoming(30)
    assert [i.id for i in overdue] == [t.id]  # trial ended and wasn't rolled: needs a decision
    kept = store.update(t.id, kind="subscription")
    assert kept.kind == "subscription"
    assert store.list_items()[0].due_date == "2026-02-20"  # now behaves as a subscription


def test_price_history_and_increases(store, clock):
    a = add_netflix(store)
    store.update(a.id, amount=17.99)
    clock.d = date(2026, 6, 1)
    store.update(a.id, amount=19.99)
    store.update(a.id, notes="no price change here")
    assert store.price_history(a.id) == [
        {"from": 15.49, "to": 17.99, "on": "2026-01-15"},
        {"from": 17.99, "to": 19.99, "on": "2026-06-01"},
    ]
    [(item, first, now)] = store.price_increases()
    assert (item.name, first, now) == ("Netflix", 15.49, 19.99)


def test_forecast_by_month(store):
    add_netflix(store, due_date="2026-01-20")  # today is 2026-01-15
    store.add(name="Insurance", kind="bill", due_date="2026-03-09", amount=600, cycle="yearly")
    store.add(name="Trial", kind="trial", due_date="2026-02-10", amount=5, cycle="monthly")
    store.add(name="Passport", kind="document", due_date="2026-02-01")
    f = store.forecast(3)
    assert [b["month"] for b in f] == ["2026-01", "2026-02", "2026-03"]
    assert [b["totals"].get("USD") for b in f] == [15.49, 20.49, 620.49]
    assert f[1]["trials"] == ["Trial"]
    with pytest.raises(ValueError):
        store.forecast(0)


def test_migrates_schema_1_database(tmp_path, clock):
    path = tmp_path / "v1.db"
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE items (id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
            kind TEXT NOT NULL CHECK (kind IN ('subscription','bill','warranty','document','renewal')),
            category TEXT NOT NULL DEFAULT '', amount_cents INTEGER NOT NULL DEFAULT 0,
            currency TEXT NOT NULL DEFAULT 'USD', cycle TEXT NOT NULL DEFAULT 'once', due_date TEXT NOT NULL,
            anchor_day INTEGER, remind_days INTEGER NOT NULL DEFAULT 7, last_used TEXT,
            notes TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (date('now')), updated_at TEXT NOT NULL DEFAULT (date('now')));
        INSERT INTO items (name, kind, amount_cents, cycle, due_date, anchor_day)
            VALUES ('Gym', 'subscription', 3999, 'monthly', '2026-01-31', 31);
        PRAGMA user_version=1;
    """)
    c.close()
    s = Store(path, today=clock)
    [gym] = s.list_items()
    assert gym.amount == 39.99
    s.add(name="Trial", kind="trial", due_date="2026-02-01")  # the new kind is allowed after migration
    s.update(gym.id, amount=44.99)
    assert s.price_history(gym.id)[0]["to"] == 44.99


def test_scan_statement_compares_with_tracked_items(store, clock):
    from pathlib import Path

    clock.d = date(2026, 11, 25)
    sample = (Path(__file__).parent.parent / "examples" / "sample-statement.csv").read_text()
    add_netflix(store, due_date="2026-12-02")  # tracked at the old price
    store.add(name="Spotify", kind="subscription", due_date="2026-12-03", amount=11.99, cycle="monthly")
    res = store.scan_statement(sample)
    assert [i.name for _, i in res["changed"]] == ["Netflix"]
    assert [i.name for _, i in res["known"]] == ["Spotify"]
    assert [r.merchant for r in res["ended"]] == ["Hellofresh"]
    assert "Planet Fitness" in {r.merchant for r in res["new"]}
    assert len(store.list_items()) == 2  # scanning never stores anything
