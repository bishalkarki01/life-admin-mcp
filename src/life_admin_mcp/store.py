"""SQLite-backed storage with validation and schema migrations.

All business logic lives here so it can be tested without an MCP client.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .dates import CYCLES_PER_YEAR, add_months, advance, next_on_or_after, parse_date
from .ics import Event, build_calendar
from .statements import Recurring, ScanResult, scan

log = logging.getLogger(__name__)

KINDS = ("subscription", "bill", "warranty", "document", "renewal", "trial")
CYCLES = tuple(CYCLES_PER_YEAR)
EXPIRING_KINDS = ("warranty", "document")
CHARGE_KINDS = ("subscription", "bill", "renewal")
NON_ROLLING_KINDS = ("trial",)  # a trial ending is an event, not something to roll forward

MAX_NAME = 200
MAX_CATEGORY = 50
MAX_NOTES = 2000
MAX_AMOUNT = 1_000_000_000
MAX_DAYS = 3650
MAX_ITEMS = 10_000
SCHEMA_VERSION = 2
_CURRENCY = re.compile(r"^[A-Z]{3}$")


class NotFoundError(LookupError):
    """Raised when an item id does not exist."""


@dataclass
class Item:
    id: int
    name: str
    kind: str
    category: str
    amount: float
    currency: str
    cycle: str
    due_date: str
    remind_days: int
    last_used: str | None
    notes: str
    active: bool
    created_at: str
    updated_at: str

    @property
    def annual_cost(self) -> float:
        return round(self.amount * CYCLES_PER_YEAR.get(self.cycle, 0), 2)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- validation
def _text(value: str, field: str, max_len: int, required: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    value = " ".join(value.split()) if field != "notes" else value.strip()
    if required and not value:
        raise ValueError(f"{field} is required")
    if len(value) > max_len:
        raise ValueError(f"{field} must be at most {max_len} characters")
    return value


def _choice(value: str, field: str, options: tuple[str, ...]) -> str:
    if value not in options:
        raise ValueError(f"{field} must be one of: {', '.join(options)} (got {value!r})")
    return value


def _amount(value: float) -> int:
    try:
        v = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("amount must be a number") from exc
    if v != v or v < 0 or v > MAX_AMOUNT:  # v != v catches NaN
        raise ValueError(f"amount must be between 0 and {MAX_AMOUNT:,}")
    return round(v * 100)


def _currency(value: str) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValueError(f"currency must be a 3-letter ISO 4217 code like USD, EUR or INR (got {value!r})")
    value = value.strip().upper()
    if not _CURRENCY.match(value):
        raise ValueError(f"currency must be a 3-letter ISO 4217 code like USD, EUR or INR (got {value!r})")
    return value


def _int_range(value: int, field: str, lo: int, hi: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be a whole number")
    if not lo <= value <= hi:
        raise ValueError(f"{field} must be between {lo} and {hi}")
    return value


def _words(name: str) -> list[str]:
    return re.findall(r"[a-z0-9&+']+", name.lower())


def _match_tracked(r: Recurring, tracked: list[Item]) -> Item | None:
    """Find the tracked item a detected charge belongs to, or None if it is unclear.

    An exact name wins. Otherwise one name must contain the other as whole words; if several
    items match that way (Apple Music and Apple iCloud), prefer the one with the same amount,
    and give up rather than guess.
    """
    key = _words(r.merchant)
    if not key:
        return None
    exact = [i for i in tracked if _words(i.name) == key]
    if len(exact) == 1:
        return exact[0]

    def contains(a: list[str], b: list[str]) -> bool:
        return any(a[k : k + len(b)] == b for k in range(len(a) - len(b) + 1))

    cands = exact or [i for i in tracked if (w := _words(i.name)) and (contains(w, key) or contains(key, w))]
    if not cands:  # "Spotify Stockholm" on the statement vs "Spotify Premium" tracked
        cands = [i for i in tracked if (w := _words(i.name)) and w[0] == key[0]]
    if len(cands) == 1:
        return cands[0]
    same = [i for i in cands if i.currency == r.currency and abs(i.amount - r.amount) <= 0.009]
    return same[0] if len(same) == 1 else None


# --------------------------------------------------------------------------- store
class Store:
    def __init__(self, path: Path | str, today: Callable[[], date] = date.today) -> None:
        self.path = Path(path).expanduser()
        self._today = today
        parent = self.path.parent
        created = not parent.exists()
        parent.mkdir(parents=True, exist_ok=True)
        if created or parent == Path.home() / ".life-admin":
            # Only tighten a folder we own: never change permissions on a directory the user chose.
            try:
                parent.chmod(0o700)
            except OSError:  # e.g. shared or read-only parent; not fatal
                pass
        if not self.path.exists():
            try:
                os.close(os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600))
            except OSError:
                pass
        self._migrate()
        for suffix in ("", "-wal", "-shm"):
            try:
                Path(f"{self.path}{suffix}").chmod(0o600)
            except OSError:  # missing or not ours; not fatal
                pass

    # ---- connection handling
    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _migrate(self) -> None:
        with self._conn() as c:
            c.execute("PRAGMA journal_mode=WAL")
            version = c.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"Database {self.path} was created by a newer version (schema {version}). "
                    "Please upgrade life-admin-mcp."
                )
            has_items = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='items'").fetchone()
            if not has_items:  # brand-new database: create the latest schema directly
                self._create_schema(c)
            else:
                if version < 1:
                    self._migrate_0_to_1(c)
                if version < 2:
                    self._migrate_1_to_2(c)
            c.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def _migrate_0_to_1(self, c: sqlite3.Connection) -> None:
        """v0 (prototype): REAL amounts, no anchor day, `created` column."""
        log.info("Migrating prototype database to schema 1")
        c.execute("ALTER TABLE items RENAME TO items_v0")
        self._create_schema(c)
        c.execute(
            """INSERT INTO items (id, name, kind, category, amount_cents, currency, cycle,
                   due_date, anchor_day, remind_days, last_used, notes, active, created_at, updated_at)
               SELECT id, name, kind, COALESCE(category,''), CAST(ROUND(COALESCE(amount,0)*100) AS INTEGER),
                   COALESCE(currency,'USD'), COALESCE(cycle,'once'), due_date,
                   CAST(substr(due_date, 9, 2) AS INTEGER), COALESCE(remind_days,7), last_used,
                   COALESCE(notes,''), COALESCE(active,1), COALESCE(created, date('now')), date('now')
               FROM items_v0"""
        )
        c.execute("DROP TABLE items_v0")
        c.execute("PRAGMA user_version=2")  # _create_schema already built the v2 layout

    def _migrate_1_to_2(self, c: sqlite3.Connection) -> None:
        """v2: allow kind='trial' (SQLite can't alter a CHECK constraint, so rebuild) and add price history."""
        if c.execute("PRAGMA user_version").fetchone()[0] >= 2:
            return
        log.info("Migrating database to schema 2")
        c.execute("ALTER TABLE items RENAME TO items_v1")
        c.execute("DROP INDEX IF EXISTS idx_items_active_due")
        self._create_schema(c)
        c.execute(
            """INSERT INTO items (id, name, kind, category, amount_cents, currency, cycle, due_date, anchor_day,
                   remind_days, last_used, notes, active, created_at, updated_at)
               SELECT id, name, kind, category, amount_cents, currency, cycle, due_date, anchor_day,
                   remind_days, last_used, notes, active, created_at, updated_at FROM items_v1"""
        )
        c.execute("DROP TABLE items_v1")

    @staticmethod
    def _create_schema(c: sqlite3.Connection) -> None:
        kinds = ",".join(f"'{k}'" for k in KINDS)
        cycles = ",".join(f"'{k}'" for k in CYCLES)
        c.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS items (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                name         TEXT    NOT NULL CHECK (length(name) BETWEEN 1 AND {MAX_NAME}),
                kind         TEXT    NOT NULL CHECK (kind IN ({kinds})),
                category     TEXT    NOT NULL DEFAULT '',
                amount_cents INTEGER NOT NULL DEFAULT 0 CHECK (amount_cents >= 0),
                currency     TEXT    NOT NULL DEFAULT 'USD',
                cycle        TEXT    NOT NULL DEFAULT 'once' CHECK (cycle IN ({cycles})),
                due_date     TEXT    NOT NULL,
                anchor_day   INTEGER,
                remind_days  INTEGER NOT NULL DEFAULT 7,
                last_used    TEXT,
                notes        TEXT    NOT NULL DEFAULT '',
                active       INTEGER NOT NULL DEFAULT 1,
                created_at   TEXT    NOT NULL DEFAULT (date('now')),
                updated_at   TEXT    NOT NULL DEFAULT (date('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_items_active_due ON items(active, due_date);
            CREATE TABLE IF NOT EXISTS price_changes (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id    INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
                old_cents  INTEGER NOT NULL,
                new_cents  INTEGER NOT NULL,
                changed_on TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_price_changes_item ON price_changes(item_id);
            """
        )

    # ---- helpers
    @staticmethod
    def _row(r: sqlite3.Row) -> Item:
        return Item(
            id=r["id"],
            name=r["name"],
            kind=r["kind"],
            category=r["category"],
            amount=r["amount_cents"] / 100,
            currency=r["currency"],
            cycle=r["cycle"],
            due_date=r["due_date"],
            remind_days=r["remind_days"],
            last_used=r["last_used"],
            notes=r["notes"],
            active=bool(r["active"]),
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        )

    def today(self) -> date:
        return self._today()

    def _roll_forward(self, c: sqlite3.Connection) -> None:
        """Move recurring items whose date has passed to their next occurrence."""
        today = self.today()
        rows = c.execute(
            "SELECT id, due_date, cycle, anchor_day FROM items "
            "WHERE active=1 AND cycle!='once' AND kind!='trial' AND due_date < ?",
            (today.isoformat(),),
        ).fetchall()
        for r in rows:
            nxt = next_on_or_after(parse_date(r["due_date"]), r["cycle"], today, r["anchor_day"])
            c.execute(
                "UPDATE items SET due_date=?, updated_at=? WHERE id=?",
                (nxt.isoformat(), today.isoformat(), r["id"]),
            )

    def _get(self, c: sqlite3.Connection, item_id: int) -> Item:
        r = c.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        if r is None:
            raise NotFoundError(f"No item with id {item_id}. Use list_items to see valid ids.")
        return self._row(r)

    # ---- commands
    def add(
        self,
        *,
        name: str,
        kind: str,
        due_date: str,
        amount: float = 0,
        currency: str = "USD",
        cycle: str = "once",
        category: str = "",
        remind_days: int = 7,
        notes: str = "",
    ) -> Item:
        name = _text(name, "name", MAX_NAME, required=True)
        kind = _choice(kind, "kind", KINDS)
        cycle = _choice(cycle, "cycle", CYCLES)
        due = parse_date(due_date, "due_date")
        cents = _amount(amount)
        cur = _currency(currency)
        category = _text(category, "category", MAX_CATEGORY).lower()
        remind_days = _int_range(remind_days, "remind_days", 0, MAX_DAYS)
        notes = _text(notes, "notes", MAX_NOTES)
        with self._conn() as c:
            if c.execute("SELECT COUNT(*) FROM items").fetchone()[0] >= MAX_ITEMS:
                raise ValueError(f"Limit of {MAX_ITEMS:,} items reached; delete old items first.")
            dup = c.execute(
                "SELECT id FROM items WHERE active=1 AND lower(name)=lower(?) AND kind=?", (name, kind)
            ).fetchone()
            if dup:
                raise ValueError(
                    f"An active {kind} named {name!r} already exists (id {dup['id']}). Use update_item to change it."
                )
            today = self.today().isoformat()
            cur_ = c.execute(
                """INSERT INTO items (name, kind, category, amount_cents, currency, cycle, due_date,
                       anchor_day, remind_days, notes, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (name, kind, category, cents, cur, cycle, due.isoformat(), due.day, remind_days, notes, today, today),
            )
            assert cur_.lastrowid is not None  # noqa: S101 - sqlite always sets it after INSERT
            return self._get(c, cur_.lastrowid)

    def update(self, item_id: int, **changes: Any) -> Item:
        cleaners: dict[str, Callable[[Any], Any]] = {
            "name": lambda v: _text(v, "name", MAX_NAME, required=True),
            "kind": lambda v: _choice(v, "kind", KINDS),
            "due_date": lambda v: parse_date(v, "due_date").isoformat(),
            "amount": _amount,
            "currency": _currency,
            "cycle": lambda v: _choice(v, "cycle", CYCLES),
            "category": lambda v: _text(v, "category", MAX_CATEGORY).lower(),
            "remind_days": lambda v: _int_range(v, "remind_days", 0, MAX_DAYS),
            "notes": lambda v: _text(v, "notes", MAX_NOTES),
        }
        unknown = set(changes) - set(cleaners)
        if unknown:
            raise ValueError(f"Unknown field(s): {', '.join(sorted(unknown))}")
        fields = {k: cleaners[k](v) for k, v in changes.items() if v is not None}
        if not fields:
            raise ValueError("Nothing to update: pass at least one field to change.")
        if "amount" in fields:
            fields["amount_cents"] = fields.pop("amount")
        if "due_date" in fields:
            fields["anchor_day"] = int(fields["due_date"][8:10])
        fields["updated_at"] = self.today().isoformat()
        with self._conn() as c:
            before = self._get(c, item_id)
            new_cents = fields.get("amount_cents")
            old_cents = round(before.amount * 100)
            if new_cents is not None and new_cents != old_cents:
                c.execute(
                    "INSERT INTO price_changes (item_id, old_cents, new_cents, changed_on) VALUES (?,?,?,?)",
                    (item_id, old_cents, new_cents, fields["updated_at"]),
                )
            sets = ", ".join(f"{k}=?" for k in fields)  # keys come from a fixed allow-list
            c.execute(f"UPDATE items SET {sets} WHERE id=?", (*fields.values(), item_id))
            return self._get(c, item_id)

    def set_active(self, item_id: int, active: bool) -> Item:
        with self._conn() as c:
            self._get(c, item_id)
            c.execute(
                "UPDATE items SET active=?, updated_at=? WHERE id=?",
                (int(active), self.today().isoformat(), item_id),
            )
            return self._get(c, item_id)

    def mark_used(self, item_id: int, used_on: str | None = None) -> Item:
        d = parse_date(used_on, "used_on") if used_on else self.today()
        if d > self.today():
            raise ValueError("used_on cannot be in the future")
        with self._conn() as c:
            item = self._get(c, item_id)
            if item.kind != "subscription":
                raise ValueError(f"Item {item_id} is a {item.kind}; only subscriptions track usage.")
            c.execute(
                "UPDATE items SET last_used=?, updated_at=? WHERE id=?",
                (d.isoformat(), self.today().isoformat(), item_id),
            )
            return self._get(c, item_id)

    def delete(self, item_id: int) -> Item:
        with self._conn() as c:
            item = self._get(c, item_id)
            c.execute("DELETE FROM items WHERE id=?", (item_id,))
            return item

    # ---- queries
    def list_items(self, kind: str | None = None, include_inactive: bool = False) -> list[Item]:
        if kind is not None:
            _choice(kind, "kind", KINDS)
        with self._conn() as c:
            self._roll_forward(c)
            q, args = "SELECT * FROM items WHERE 1=1", []
            if not include_inactive:
                q += " AND active=1"
            if kind:
                q += " AND kind=?"
                args.append(kind)
            return [self._row(r) for r in c.execute(q + " ORDER BY due_date, id", args)]

    def upcoming(self, days: int = 30) -> tuple[list[Item], list[Item]]:
        """Return (overdue/expired one-off items, items due within the window)."""
        days = _int_range(days, "days", 1, MAX_DAYS)
        today = self.today()
        overdue, soon = [], []
        for it in self.list_items():
            left = (parse_date(it.due_date) - today).days
            if left < 0:
                overdue.append(it)
            elif left <= max(days, it.remind_days):
                soon.append(it)
        return overdue, soon

    def spending(self) -> dict[str, Any]:
        recurring = [i for i in self.list_items() if i.cycle != "once" and i.amount > 0 and i.kind != "trial"]
        by_currency: dict[str, float] = {}
        by_category: dict[str, dict[str, float]] = {}
        for i in recurring:
            by_currency[i.currency] = round(by_currency.get(i.currency, 0) + i.annual_cost, 2)
            cat = by_category.setdefault(i.category or "uncategorised", {})
            cat[i.currency] = round(cat.get(i.currency, 0) + i.annual_cost, 2)
        return {
            "per_currency": {cur: {"monthly": round(a / 12, 2), "yearly": a} for cur, a in by_currency.items()},
            "by_category": by_category,
            "top": sorted(recurring, key=lambda i: i.annual_cost, reverse=True)[:5],
            "trials": [i for i in self.list_items(kind="trial") if i.amount > 0],
            "increases": self.price_increases(),
        }

    def savings(self, unused_days: int = 30) -> dict[str, Any]:
        unused_days = _int_range(unused_days, "unused_days", 1, MAX_DAYS)
        today = self.today()
        subs = self.list_items(kind="subscription")
        stale = [s for s in subs if s.last_used is None or (today - parse_date(s.last_used)).days > unused_days]
        groups: dict[str, list[Item]] = {}
        for s in subs:
            if s.category:
                groups.setdefault(s.category, []).append(s)
        potential: dict[str, float] = {}
        for s in stale:
            potential[s.currency] = round(potential.get(s.currency, 0) + s.annual_cost, 2)
        trials = [
            t
            for t in self.list_items(kind="trial")
            if 0 <= (parse_date(t.due_date) - today).days <= max(unused_days, t.remind_days)
        ]
        return {
            "trials": trials,
            "stale": stale,
            "overlaps": {k: v for k, v in groups.items() if len(v) > 1},
            "potential_yearly": potential,
        }

    def calendar(self, days: int = 365) -> str:
        days = _int_range(days, "days", 1, MAX_DAYS)
        today = self.today()
        events = []
        for it, d in self.occurrences(today, today + timedelta(days=days)):
            money = f" — {it.amount:.2f} {it.currency}" if it.amount else ""
            if it.kind == "trial" and d.isoformat() == it.due_date:
                summary = f"{it.name} free trial ends — then{money}/{it.cycle}" if money else f"{it.name} trial ends"
            elif it.kind == "trial":  # the charges that follow if the trial isn't cancelled
                summary = f"{it.name} due{money} (if the trial wasn't cancelled)"
            else:
                verb = "expires" if it.kind in EXPIRING_KINDS else "due"
                summary = f"{it.name} {verb}{money}"
            events.append(
                Event(
                    uid=f"life-admin-{it.id}-{d:%Y%m%d}@life-admin-mcp",
                    day=d,
                    summary=summary,
                    description=it.notes,
                    alarm_days_before=it.remind_days,
                )
            )
        return build_calendar(events)

    def price_history(self, item_id: int) -> list[dict[str, Any]]:
        with self._conn() as c:
            self._get(c, item_id)
            rows = c.execute(
                "SELECT old_cents, new_cents, changed_on FROM price_changes WHERE item_id=? ORDER BY id",
                (item_id,),
            ).fetchall()
        return [{"from": r["old_cents"] / 100, "to": r["new_cents"] / 100, "on": r["changed_on"]} for r in rows]

    def get(self, item_id: int) -> Item:
        with self._conn() as c:
            return self._get(c, item_id)

    def price_increases(self) -> list[tuple[Item, float, float]]:
        """Active items now costing more than when first tracked: (item, first price, current price)."""
        with self._conn() as c:
            rows = c.execute(
                """SELECT p.item_id, p.old_cents AS first_cents FROM price_changes p
                   WHERE p.id = (SELECT MIN(id) FROM price_changes q WHERE q.item_id = p.item_id)"""
            ).fetchall()
            items = {r["id"]: self._row(r) for r in c.execute("SELECT * FROM items WHERE active=1")}
        out = []
        for r in rows:
            item = items.get(r["item_id"])
            first = r["first_cents"] / 100
            if item and item.amount > first:
                out.append((item, first, item.amount))
        return sorted(out, key=lambda x: (x[2] - x[1]) / max(x[1], 0.01), reverse=True)

    def occurrences(self, start: date, end: date) -> list[tuple[Item, date]]:
        """Every charge or expiry between start and end (inclusive), with recurrences expanded.
        A trial appears on its end date and then repeats on its billing cycle, which is what
        happens if nobody cancels it."""
        with self._conn() as c:
            anchors = {r["id"]: r["anchor_day"] for r in c.execute("SELECT id, anchor_day FROM items")}
        out: list[tuple[Item, date]] = []
        for it in self.list_items():
            d = parse_date(it.due_date)
            for _ in range(600):  # weekly for ~11 years at most
                if d > end:
                    break
                if d >= start:
                    out.append((it, d))
                if it.cycle == "once":
                    break
                d = advance(d, it.cycle, anchors.get(it.id))
        out.sort(key=lambda x: (x[1], x[0].id))
        return out

    def forecast(self, months: int = 6) -> list[dict[str, Any]]:
        """Money going out per calendar month, from today to the end of the last month."""
        months = _int_range(months, "months", 1, 36)
        today = self.today()
        first = today.replace(day=1)
        end = add_months(first, months) - timedelta(days=1)
        buckets: dict[str, dict[str, Any]] = {}
        for i in range(months):
            key = add_months(first, i).strftime("%Y-%m")
            buckets[key] = {"month": key, "totals": {}, "items": [], "trials": []}
        for it, d in self.occurrences(today, end):
            if not it.amount or it.kind in EXPIRING_KINDS:
                continue
            b = buckets[d.strftime("%Y-%m")]
            b["totals"][it.currency] = round(b["totals"].get(it.currency, 0) + it.amount, 2)
            b["items"].append((it, d))
            if it.kind == "trial" and it.name not in b["trials"]:
                b["trials"].append(it.name)
        return list(buckets.values())

    def scan_statement(self, text: str, date_format: str = "auto", default_currency: str = "USD") -> dict[str, Any]:
        """Detect recurring charges and compare them with tracked items. Nothing is stored."""
        result: ScanResult = scan(text, self.today(), date_format, _currency(default_currency))
        tracked = self.list_items()
        new: list[Recurring] = []
        known: list[tuple[Recurring, Item]] = []
        changed: list[tuple[Recurring, Item]] = []
        ended: list[Recurring] = []
        for r in result.recurring:
            if r.ended:
                ended.append(r)
                continue
            match = _match_tracked(r, tracked)
            if match is None:
                new.append(r)
            elif match.currency == r.currency and abs(match.amount - r.amount) > 0.009:
                changed.append((r, match))
            else:
                known.append((r, match))
        return {"result": result, "new": new, "known": known, "changed": changed, "ended": ended}

    # ---- backup
    def export_json(self) -> str:
        items = [i.to_dict() for i in self.list_items(include_inactive=True)]
        return json.dumps(
            {"format": "life-admin-mcp", "schema": SCHEMA_VERSION, "items": items},
            indent=2,
            ensure_ascii=False,
        )

    def import_json(self, data: str) -> tuple[int, list[str]]:
        """Merge items from export_json output. Skips duplicates; returns (added, errors)."""
        try:
            payload = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Not valid JSON: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("format") != "life-admin-mcp":
            raise ValueError("This is not a life-admin-mcp export (missing format marker).")
        items = payload.get("items")
        if not isinstance(items, list):
            raise ValueError("Export has no 'items' list.")
        added, errors = 0, []
        for n, raw in enumerate(items, 1):
            try:
                if not isinstance(raw, dict):
                    raise ValueError("item is not an object")
                item = self.add(
                    name=raw["name"],
                    kind=raw["kind"],
                    due_date=raw["due_date"],
                    amount=raw.get("amount", 0),
                    currency=raw.get("currency", "USD"),
                    cycle=raw.get("cycle", "once"),
                    category=raw.get("category", ""),
                    remind_days=raw.get("remind_days", 7),
                    notes=raw.get("notes", ""),
                )
                if raw.get("last_used"):
                    with self._conn() as c:
                        c.execute(
                            "UPDATE items SET last_used=? WHERE id=?",
                            (parse_date(raw["last_used"], "last_used").isoformat(), item.id),
                        )
                if raw.get("active") is False:
                    self.set_active(item.id, False)
                added += 1
            except (KeyError, ValueError) as exc:
                errors.append(f"item {n}: {exc}")
        return added, errors
