"""MCP server exposing the life-admin store as tools, a resource and a prompt."""

from __future__ import annotations

import argparse
import functools
import logging
import os
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any, Literal, Optional, TypeVar

from mcp.types import ToolAnnotations

try:  # MCP Python SDK v2
    from mcp.server.mcpserver import MCPServer as _Server
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # MCP Python SDK v1
    from mcp.server.fastmcp import FastMCP as _Server  # type: ignore[attr-defined,no-redef]
    from mcp.server.fastmcp.exceptions import ToolError  # type: ignore[no-redef]

from . import __version__
from .store import EXPIRING_KINDS, Item, Store

log = logging.getLogger("life_admin_mcp")

Kind = Literal["subscription", "bill", "warranty", "document", "renewal", "trial"]
Cycle = Literal["weekly", "monthly", "quarterly", "yearly", "once"]

DEFAULT_DB = Path.home() / ".life-admin" / "life_admin.db"

INSTRUCTIONS = (
    "Tracks the user's subscriptions, free trials, bills, warranties, expiring documents and renewals. "
    "Dates are YYYY-MM-DD; convert relative dates ('next Friday') yourself before calling tools. "
    "Use get_upcoming for 'what's due', find_savings for 'what can I cancel', spending_summary for totals, "
    "forecast for future months. If the user has a bank or card statement, use scan_statement to find "
    "recurring charges, show them the results, and only add the items they confirm. "
    "Confirm with the user before delete_item. Never invent amounts or dates: ask if unknown."
)

mcp = _Server("life-admin", instructions=INSTRUCTIONS)

_store: Store | None = None


def get_store() -> Store:
    global _store
    if _store is None:
        _store = Store(os.environ.get("LIFE_ADMIN_DB", DEFAULT_DB))
    return _store


def set_store(store: Store) -> None:
    """Inject a store (used by tests and main())."""
    global _store
    _store = store


# --------------------------------------------------------------------------- formatting
def _when(item: Item, today: date) -> str:
    days = (date.fromisoformat(item.due_date) - today).days
    if days == 0:
        return "today"
    if days == 1:
        return "tomorrow"
    return f"in {days} days" if days > 0 else f"{-days} days ago"


def fmt(item: Item, today: date | None = None) -> str:
    today = today or get_store().today()
    money = f" — {item.amount:.2f} {item.currency}/{item.cycle}" if item.amount else ""
    verb = "expires" if item.kind in EXPIRING_KINDS else "due"
    if item.kind == "trial":
        verb = "free trial ends"
        money = f" — then {item.amount:.2f} {item.currency}/{item.cycle}" if item.amount else ""
    cat = f" [{item.category}]" if item.category else ""
    used = f", last used {item.last_used}" if item.last_used else ""
    state = "" if item.active else " (inactive)"
    note = f"\n    note: {item.notes}" if item.notes else ""
    return (
        f"#{item.id} {item.name} ({item.kind}){cat}{money} — {verb} {item.due_date} "
        f"({_when(item, today)}){used}{state}{note}"
    )


def _money(d: dict[str, float]) -> str:
    return ", ".join(f"{v:.2f} {k}" for k, v in sorted(d.items())) or "0"


def _hints(*, read_only: bool, destructive: bool, idempotent: bool) -> ToolAnnotations:
    # Wire-format (camelCase) keys validate on both SDK v1 and v2.
    return ToolAnnotations.model_validate(
        {
            "readOnlyHint": read_only,
            "destructiveHint": destructive,
            "idempotentHint": idempotent,
            "openWorldHint": False,
        }
    )


RO = _hints(read_only=True, destructive=False, idempotent=True)
WRITE = _hints(read_only=False, destructive=False, idempotent=False)
IDEMPOTENT = _hints(read_only=False, destructive=False, idempotent=True)
DESTRUCTIVE = _hints(read_only=False, destructive=True, idempotent=True)


F = TypeVar("F", bound=Callable[..., Any])


def tool(annotations: ToolAnnotations) -> Callable[[F], F]:
    """Register a tool whose expected failures (bad input, unknown id) reach the
    assistant as a readable message. The SDK would otherwise hide the message."""

    def decorate(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return fn(*args, **kwargs)
            except (ValueError, LookupError) as exc:
                # KeyError's str() adds quotes; use the raw message instead.
                msg = exc.args[0] if exc.args and isinstance(exc.args[0], str) else str(exc)
                raise ToolError(msg) from exc

        mcp.tool(annotations=annotations)(wrapper)
        return fn

    return decorate


# --------------------------------------------------------------------------- tools
@tool(WRITE)
def add_item(
    name: str,
    kind: Kind,
    due_date: str,
    amount: float = 0,
    currency: str = "USD",
    cycle: Cycle = "once",
    category: str = "",
    remind_days: int = 7,
    notes: str = "",
) -> str:
    """Start tracking a subscription, bill, warranty, document or renewal.

    Args:
        name: e.g. "Netflix", "Passport", "Car insurance".
        kind: subscription | bill | warranty | document | renewal | trial.
            For a free trial use kind="trial", due_date = the day it ends, and amount/cycle = what it
            will cost afterwards.
        due_date: Next charge date, or the expiry date for warranties and documents (YYYY-MM-DD).
        amount: Cost per cycle. Use 0 for documents and warranties.
        currency: 3-letter ISO code, e.g. USD, EUR, GBP, INR.
        cycle: weekly | monthly | quarterly | yearly | once. Warranties and documents are usually "once".
        category: Optional group such as "streaming", "insurance", "utilities". Used to spot overlaps.
        remind_days: How many days ahead it should appear in get_upcoming (passports: about 180).
        notes: Anything useful, such as an account number hint or how to cancel.
    """
    item = get_store().add(
        name=name,
        kind=kind,
        due_date=due_date,
        amount=amount,
        currency=currency,
        cycle=cycle,
        category=category,
        remind_days=remind_days,
        notes=notes,
    )
    return "Added:\n" + fmt(item)


@tool(RO)
def list_items(kind: Optional[Kind] = None, include_inactive: bool = False) -> str:
    """List tracked items, soonest first. Optionally filter by kind or include cancelled ones."""
    items = get_store().list_items(kind=kind, include_inactive=include_inactive)
    if not items:
        return "Nothing tracked yet. Use add_item to start."
    return f"{len(items)} item(s):\n" + "\n".join(fmt(i) for i in items)


@tool(RO)
def get_upcoming(days: int = 30) -> str:
    """Show what needs attention: charges, renewals and expiries in the next `days` days
    (1-3650), plus items whose own reminder window has started. Expired one-off items,
    such as a lapsed passport or warranty, are listed first."""
    store = get_store()
    today = store.today()
    overdue, soon = store.upcoming(days)
    in_window = [i for i in soon if (date.fromisoformat(i.due_date) - today).days <= days]
    heads_up = [i for i in soon if i not in in_window]
    out = []
    if overdue:
        out.append("⚠️ Expired / overdue:\n" + "\n".join(fmt(i) for i in overdue))
    if in_window:
        totals: dict[str, float] = {}
        for i in in_window:
            if i.kind not in EXPIRING_KINDS and i.amount:
                totals[i.currency] = round(totals.get(i.currency, 0) + i.amount, 2)
        block = f"Due in the next {days} days:\n" + "\n".join(fmt(i) for i in in_window)
        if totals:
            block += f"\nTotal charges in the next {days} days: {_money(totals)}"
        out.append(block)
    if heads_up:
        out.append("Heads-up (later, but inside its reminder window):\n" + "\n".join(fmt(i) for i in heads_up))
    return "\n\n".join(out) or f"Nothing due in the next {days} days."


@tool(RO)
def spending_summary() -> str:
    """Monthly and yearly cost of all active recurring items, by currency and category,
    plus the five most expensive."""
    s = get_store().spending()
    if not s["per_currency"]:
        return "No recurring costs tracked."
    lines = ["Recurring costs:"]
    for cur, v in sorted(s["per_currency"].items()):
        lines.append(f"  {cur}: {v['monthly']:.2f}/month · {v['yearly']:.2f}/year")
    lines.append("\nBy category (per year):")
    for cat, per in sorted(s["by_category"].items(), key=lambda kv: -sum(kv[1].values())):
        lines.append(f"  {cat}: {_money(per)}")
    lines.append("\nMost expensive (per year):")
    for i in s["top"]:
        lines.append(f"  #{i.id} {i.name}: {i.annual_cost:.2f} {i.currency}")
    if s["increases"]:
        lines.append("\nPrice increases since you started tracking:")
        for i, first, now in s["increases"]:
            pct = f" (+{(now - first) / first:.0%})" if first > 0 else ""
            lines.append(f"  #{i.id} {i.name}: {first:.2f} → {now:.2f} {i.currency}{pct}")
    if s["trials"]:
        lines.append("\nFree trials (not in the totals yet) that will start charging:")
        for i in s["trials"]:
            lines.append(f"  #{i.id} {i.name}: {i.amount:.2f} {i.currency}/{i.cycle} from {i.due_date}")
    return "\n".join(lines)


@tool(RO)
def find_savings(unused_days: int = 30) -> str:
    """Suggest subscriptions to cancel: ones not used in `unused_days` days (or never marked
    used) and categories with more than one subscription. Ask the user before cancelling."""
    s = get_store().savings(unused_days)
    out = []
    if s["trials"]:
        out.append(
            "Free trials ending soon (cancel before the date to avoid being charged):\n"
            + "\n".join(
                f"  #{i.id} {i.name} — ends {i.due_date}, then {i.amount:.2f} {i.currency}/{i.cycle}"
                for i in s["trials"]
            )
        )
    if s["stale"]:
        out.append(
            f"Not used in {unused_days}+ days (or usage never recorded):\n"
            + "\n".join(
                f"  #{i.id} {i.name} — last used {i.last_used or 'never recorded'} — "
                f"{i.annual_cost:.2f} {i.currency}/yr"
                for i in s["stale"]
            )
            + f"\n  Potential saving: {_money(s['potential_yearly'])} per year"
        )
    if s["overlaps"]:
        out.append(
            "Possible overlap:\n"
            + "\n".join(
                f"  {cat}: " + ", ".join(f"{i.name} (#{i.id})" for i in items) for cat, items in s["overlaps"].items()
            )
        )
    return "\n\n".join(out) or "No obvious savings: every subscription looks used and unique."


@tool(IDEMPOTENT)
def mark_used(item_id: int, used_on: Optional[str] = None) -> str:
    """Record that the user used a subscription (default today, YYYY-MM-DD). Feeds find_savings."""
    return "Recorded:\n" + fmt(get_store().mark_used(item_id, used_on))


@tool(IDEMPOTENT)
def update_item(
    item_id: int,
    name: Optional[str] = None,
    kind: Optional[Kind] = None,
    due_date: Optional[str] = None,
    amount: Optional[float] = None,
    currency: Optional[str] = None,
    cycle: Optional[Cycle] = None,
    category: Optional[str] = None,
    remind_days: Optional[int] = None,
    notes: Optional[str] = None,
) -> str:
    """Change fields on an item, e.g. a price rise, a renewed passport's new expiry date, or a
    trial the user kept (set kind="subscription"). Only the fields you pass change. Price changes
    are recorded in the item's history."""
    item = get_store().update(
        item_id,
        name=name,
        kind=kind,
        due_date=due_date,
        amount=amount,
        currency=currency,
        cycle=cycle,
        category=category,
        remind_days=remind_days,
        notes=notes,
    )
    return "Updated:\n" + fmt(item)


@tool(IDEMPOTENT)
def cancel_item(item_id: int) -> str:
    """Mark an item as cancelled. It stays in history (list_items include_inactive=true)
    and stops appearing in reminders and totals. Use reactivate_item to undo."""
    return "Cancelled:\n" + fmt(get_store().set_active(item_id, False))


@tool(IDEMPOTENT)
def reactivate_item(item_id: int) -> str:
    """Undo cancel_item."""
    return "Reactivated:\n" + fmt(get_store().set_active(item_id, True))


@tool(DESTRUCTIVE)
def delete_item(item_id: int) -> str:
    """Permanently delete an item, e.g. one added by mistake. This cannot be undone,
    so confirm with the user first. Prefer cancel_item for things they stopped paying for."""
    item = get_store().delete(item_id)
    return f"Deleted #{item.id} {item.name}."


@tool(RO)
def export_calendar(days: int = 365) -> str:
    """Return an iCalendar (.ics) file's text covering the next `days` days, with an alarm
    `remind_days` before each date. Save it as a .ics file and import it into Google,
    Apple or Outlook calendars."""
    return get_store().calendar(days)


@tool(RO)
def export_data() -> str:
    """Return a JSON backup of every item, including cancelled ones. Can be restored with import_data."""
    return get_store().export_json()


@tool(WRITE)
def import_data(json_text: str) -> str:
    """Merge items from an export_data JSON backup. Active duplicates (same name and kind) are skipped."""
    added, errors = get_store().import_json(json_text)
    msg = f"Imported {added} item(s)."
    if errors:
        msg += f"\nSkipped {len(errors)}:\n" + "\n".join(f"  {e}" for e in errors[:50])
    return msg


@tool(RO)
def get_item(item_id: int) -> str:
    """Full details of one item, including its price history."""
    store = get_store()
    item = store.get(item_id)
    out = [fmt(item), f"    tracked since {item.created_at}, last changed {item.updated_at}"]
    hist = store.price_history(item_id)
    if hist:
        out.append("    price history: " + "; ".join(f"{h['on']}: {h['from']:.2f} → {h['to']:.2f}" for h in hist))
    return "\n".join(out)


@tool(RO)
def forecast(months: int = 6) -> str:
    """Money going out in each of the next `months` calendar months (1-36), including the rest of
    this month. Free trials are counted from their end date, as if they are not cancelled."""
    out = []
    for b in get_store().forecast(months):
        total = _money(b["totals"])
        biggest = sorted(b["items"], key=lambda x: -x[0].amount)[:4]
        detail = ", ".join(f"{it.name} {it.amount:.2f}" for it, _ in biggest)
        more = f" +{len(b['items']) - 4} more" if len(b["items"]) > 4 else ""
        line = f"{b['month']}: {total}" + (f"  ({detail}{more})" if detail else "")
        if b["trials"]:
            line += f"  ⚠️ includes trials converting: {', '.join(b['trials'])}"
        out.append(line)
    return "Forecast of money going out:\n" + "\n".join(out)


MAX_STATEMENT_PATH_BYTES = 5_000_000


@tool(RO)
def scan_statement(
    csv_text: Optional[str] = None,
    csv_path: Optional[str] = None,
    date_format: Literal["auto", "iso", "us", "eu", "text"] = "auto",
    default_currency: str = "USD",
) -> str:
    """Find recurring charges (subscriptions, bills) in a bank or card statement CSV, and compare them
    with what is already tracked. Nothing from the statement is stored.

    Pass either csv_text (the pasted CSV) or csv_path (a .csv file on this computer, which avoids
    pasting large statements into the chat). Works with most banks' exports: the date, description
    and amount (or debit/credit) columns are detected automatically. 3+ months of history works best,
    or 13+ months to catch yearly charges.

    Show the user the results and ask which to track before calling add_item. For items listed under
    'price changed', offer update_item.
    """
    if bool(csv_text) == bool(csv_path):
        raise ValueError("Pass exactly one of csv_text or csv_path.")
    if csv_path:
        path = Path(csv_path).expanduser()
        if path.suffix.lower() not in (".csv", ".tsv", ".txt"):
            raise ValueError("csv_path must point to a .csv, .tsv or .txt file exported from your bank.")
        if not path.is_file():
            raise ValueError(f"File not found: {path}")
        if path.stat().st_size > MAX_STATEMENT_PATH_BYTES:
            raise ValueError("File is larger than 5 MB; export a shorter date range.")
        csv_text = path.read_text(encoding="utf-8-sig", errors="replace")
    assert csv_text is not None  # noqa: S101 - one of the two was provided
    res = get_store().scan_statement(csv_text, date_format, default_currency)
    r = res["result"]
    rng = f"{r.date_range[0]} to {r.date_range[1]}" if r.date_range else "no dates"
    out = [f"Read {r.rows_read} rows ({r.charges_read} charges, {rng}). Found {len(r.recurring)} recurring charge(s)."]

    def line(x: Any) -> str:
        if x.variable:
            amount = f"≈{x.amount:.2f} {x.currency}/{x.cycle} (varies {x.variable[0]:.2f}–{x.variable[1]:.2f})"
        else:
            amount = f"{x.amount:.2f} {x.currency}/{x.cycle}"
        pc = (
            f" · price rose {x.price_change[0]:.2f} → {x.price_change[1]:.2f} on {x.price_changed_on}"
            if x.price_change and x.price_change[1] > x.price_change[0]
            else f" · price fell {x.price_change[0]:.2f} → {x.price_change[1]:.2f}"
            if x.price_change
            else ""
        )
        return (
            f"  {x.merchant}: {amount} ≈ {x.annual_cost:.2f}/yr · charged {x.occurrences}× "
            f"since {x.first_seen} · next ≈ {x.next_expected} · {x.confidence} confidence{pc}"
        )

    if res["new"]:
        total: dict[str, float] = {}
        for x in res["new"]:
            total[x.currency] = round(total.get(x.currency, 0) + x.annual_cost, 2)
        out.append(
            "\nNot tracked yet:\n"
            + "\n".join(line(x) for x in res["new"])
            + f"\n  Together: about {_money(total)} per year"
        )
    if res["changed"]:
        out.append(
            "\nTracked, but the statement shows a different price:\n"
            + "\n".join(
                f"  #{i.id} {i.name}: tracked {i.amount:.2f}, statement {x.amount:.2f} {x.currency}"
                for x, i in res["changed"]
            )
        )
    if res["known"]:
        out.append("\nAlready tracked: " + ", ".join(f"{i.name} (#{i.id})" for _, i in res["known"]))
    if res["ended"]:
        out.append(
            "\nLooks stopped (no charge for over a cycle; don't add unless the user says it's still active):\n"
            + "\n".join(
                f"  {x.merchant}: {x.amount:.2f} {x.currency}/{x.cycle}, last charged {x.last_seen}"
                for x in res["ended"]
            )
        )
    if r.warnings:
        out.append("\nNotes:\n" + "\n".join(f"  - {w}" for w in r.warnings))
    if not r.recurring:
        out.append("No regular charges detected. A longer statement (3+ months) usually helps.")
    return "\n".join(out)


# --------------------------------------------------------------------------- resource & prompt
@mcp.resource("life-admin://upcoming", mime_type="text/plain")
def upcoming_resource() -> str:
    """What's due or expiring in the next 30 days."""
    return get_upcoming(30)


@mcp.prompt()
def import_statement() -> str:
    """Find every subscription in a bank or card statement and start tracking them."""
    return (
        "Help me find all my subscriptions and recurring bills. Ask me to export my last 3-12 months "
        "of transactions as a CSV from my bank or card app and give you the file path, or paste it. "
        "Run scan_statement, then show me a short table of what you found with the yearly cost. "
        "Ask which to track, add the ones I confirm with add_item (use next_expected as the due date "
        "and a sensible category), and update any price changes I approve. Finish with find_savings."
    )


@mcp.prompt()
def weekly_review() -> str:
    """A 5-minute weekly life-admin check-in."""
    return (
        "Run my weekly life-admin review. Call get_upcoming with days=14, then find_savings, "
        "then spending_summary. Give me a short checklist of what to pay, renew or cancel this "
        "week. Then ask which of my subscriptions I used, and record them with mark_used."
    )


# --------------------------------------------------------------------------- entry point
def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="life-admin-mcp", description=__doc__)
    parser.add_argument("--db", help=f"SQLite file (default: $LIFE_ADMIN_DB or {DEFAULT_DB})")
    parser.add_argument(
        "--log-level",
        default=os.environ.get("LIFE_ADMIN_LOG_LEVEL", "WARNING"),
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="stdio (default, for desktop clients) or http (Streamable HTTP at /mcp)",
    )
    parser.add_argument("--host", default="127.0.0.1", help="HTTP host (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="HTTP port (default 8765)")
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="Allow binding HTTP to a non-loopback address. There is no authentication: "
        "only do this behind your own authenticating proxy.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)
    if args.transport == "http" and not _is_loopback(args.host) and not args.allow_remote:
        parser.error(
            f"refusing to expose your data on {args.host}: the HTTP transport has no authentication. "
            "Use 127.0.0.1, or add --allow-remote if it sits behind an authenticating proxy."
        )

    # stdout carries the MCP protocol; logs must go to stderr.
    logging.basicConfig(
        stream=sys.stderr, level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    db = args.db or os.environ.get("LIFE_ADMIN_DB") or DEFAULT_DB
    try:
        set_store(Store(db))
    except Exception as exc:  # noqa: BLE001 - report any startup failure clearly, then exit
        log.error("Could not open database %s: %s", db, exc)
        sys.exit(1)
    log.info("life-admin-mcp %s using %s", __version__, db)
    if args.transport == "stdio":
        mcp.run()
        return
    if not _is_loopback(args.host):
        log.warning("HTTP transport listening on %s with NO authentication", args.host)
    print(f"life-admin-mcp {__version__} listening on http://{args.host}:{args.port}/mcp", file=sys.stderr)
    settings = getattr(mcp, "settings", None)
    if settings is not None and hasattr(settings, "port"):  # SDK v1 configures host/port via settings
        setattr(settings, "host", args.host)  # noqa: B010
        setattr(settings, "port", args.port)  # noqa: B010
        if not _is_loopback(args.host) and hasattr(settings, "transport_security"):
            # v1 locked the Host allow-list to loopback when the server was built; behind a proxy
            # (--allow-remote) the proxy's Host header must be accepted.
            from mcp.server.transport_security import TransportSecuritySettings

            setattr(  # noqa: B010
                settings, "transport_security", TransportSecuritySettings(enable_dns_rebinding_protection=False)
            )
        mcp.run("streamable-http")
    else:  # SDK v2 takes them as run() options
        mcp.run("streamable-http", host=args.host, port=args.port)


def _is_loopback(host: str) -> bool:
    # Only the addresses the MCP SDK protects against DNS rebinding. Other loopback IPs such as
    # 127.0.0.2 would run without that protection, so they need --allow-remote like any other host.
    return host in _LOOPBACK


_LOOPBACK = ("127.0.0.1", "localhost", "::1")


if __name__ == "__main__":
    main()
