"""Re-create the demo session: drive the real server over stdio and record every call and result.

Usage:  python demo/record.py OUTPUT_DIR
Writes OUTPUT_DIR/session.json and OUTPUT_DIR/maya-life-admin.ics using a fresh database.
Dates are fixed to the original recording (25 Nov 2026), so run it with your clock near that
date, or edit the due dates, if you want the same "in N days" wording.
"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def err(r):
    return getattr(r, "is_error", getattr(r, "isError", False))


SCENES = [
    (
        "Here's my stuff: Netflix $15.49, Hulu $9.99, Max $16.99 and Spotify $11.99 monthly; gym $49; iCloud $2.99; rent $2,100 on the 1st; phone $65; car insurance $1,140 a year due Jan 9; a $12 domain renewal on Dec 20. My passport expires May 2, 2027, licence March 15, 2028. MacBook warranty ends Dec 12 and the fridge warranty ended Nov 10.",
        [
            (
                "add_item",
                dict(
                    name="Netflix",
                    kind="subscription",
                    due_date="2026-12-02",
                    amount=15.49,
                    cycle="monthly",
                    category="streaming",
                ),
            ),
            (
                "add_item",
                dict(
                    name="Hulu",
                    kind="subscription",
                    due_date="2026-12-10",
                    amount=9.99,
                    cycle="monthly",
                    category="streaming",
                ),
            ),
            (
                "add_item",
                dict(
                    name="Max",
                    kind="subscription",
                    due_date="2026-12-18",
                    amount=16.99,
                    cycle="monthly",
                    category="streaming",
                ),
            ),
            (
                "add_item",
                dict(
                    name="Spotify",
                    kind="subscription",
                    due_date="2026-12-03",
                    amount=11.99,
                    cycle="monthly",
                    category="music",
                ),
            ),
            (
                "add_item",
                dict(
                    name="Gym",
                    kind="subscription",
                    due_date="2026-12-01",
                    amount=49,
                    cycle="monthly",
                    category="fitness",
                    notes="Cancel in person at front desk, 30 days notice",
                ),
            ),
            (
                "add_item",
                dict(
                    name="iCloud+",
                    kind="subscription",
                    due_date="2026-12-14",
                    amount=2.99,
                    cycle="monthly",
                    category="storage",
                ),
            ),
            (
                "add_item",
                dict(
                    name="Rent",
                    kind="bill",
                    due_date="2026-12-01",
                    amount=2100,
                    cycle="monthly",
                    category="housing",
                    remind_days=5,
                ),
            ),
            (
                "add_item",
                dict(
                    name="Phone plan",
                    kind="bill",
                    due_date="2026-12-06",
                    amount=65,
                    cycle="monthly",
                    category="utilities",
                ),
            ),
            (
                "add_item",
                dict(
                    name="Car insurance",
                    kind="bill",
                    due_date="2027-01-09",
                    amount=1140,
                    cycle="yearly",
                    category="insurance",
                    remind_days=45,
                ),
            ),
            (
                "add_item",
                dict(
                    name="mayabakes.com domain",
                    kind="renewal",
                    due_date="2026-12-20",
                    amount=12,
                    cycle="yearly",
                    category="web",
                ),
            ),
            (
                "add_item",
                dict(
                    name="Passport",
                    kind="document",
                    due_date="2027-05-02",
                    remind_days=180,
                    notes="Renewal takes 6-8 weeks; many countries need 6 months validity",
                ),
            ),
            ("add_item", dict(name="Driver's licence", kind="document", due_date="2028-03-15", remind_days=60)),
            (
                "add_item",
                dict(
                    name="MacBook AppleCare",
                    kind="warranty",
                    due_date="2026-12-12",
                    remind_days=30,
                    notes="Battery is swelling - get it checked before this ends",
                ),
            ),
            ("add_item", dict(name="Fridge warranty", kind="warranty", due_date="2026-11-10")),
        ],
    ),
    (
        "Oh and add my yoga app, renews next Friday.",
        [
            (
                "add_item",
                dict(name="Yoga app", kind="subscription", due_date="next Friday", amount=12.99, cycle="monthly"),
            ),
            (
                "add_item",
                dict(
                    name="Yoga app",
                    kind="subscription",
                    due_date="2026-12-04",
                    amount=12.99,
                    cycle="monthly",
                    category="fitness",
                ),
            ),
        ],
    ),
    (
        "I watched Netflix on Sunday, Spotify today, Max back in early October. Gym... honestly not since mid-September.",
        [
            ("mark_used", dict(item_id=1, used_on="2026-11-22")),
            ("mark_used", dict(item_id=4)),
            ("mark_used", dict(item_id=3, used_on="2026-10-01")),
            ("mark_used", dict(item_id=5, used_on="2026-09-14")),
            ("mark_used", dict(item_id=6)),
        ],
    ),
    ("What's coming up in the next two weeks?", [("get_upcoming", dict(days=14))]),
    ("How much am I spending on all this?", [("spending_summary", {})]),
    ("What could I cancel?", [("find_savings", dict(unused_days=30))]),
    (
        "Netflix just raised my price to $17.99. And cancel Hulu, I never use it.",
        [
            ("update_item", dict(item_id=1, amount=17.99)),
            ("cancel_item", dict(item_id=2)),
        ],
    ),
    ("Okay, what am I spending now?", [("spending_summary", {})]),
    ("Put everything on my calendar for the next 3 months.", [("export_calendar", dict(days=90))]),
]


async def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    db = str(Path(tempfile.mkdtemp()) / "demo.db")
    p = StdioServerParameters(command=sys.executable, args=["-m", "life_admin_mcp", "--db", db], env={**os.environ})
    log = []
    async with stdio_client(p) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        tools = (await s.list_tools()).tools
        for user, calls in SCENES:
            scene = {"user": user, "calls": []}
            for name, args in calls:
                res = await s.call_tool(name, args)
                scene["calls"].append({"tool": name, "args": args, "result": res.content[0].text, "error": err(res)})
            log.append(scene)
    json.dump({"tools": [t.name for t in tools], "scenes": log}, open(out / "session.json", "w"), indent=1)
    cal = log[-1]["calls"][0]["result"]
    open(out / "maya-life-admin.ics", "w", newline="").write(cal)
    print(
        f"{len(tools)} tools, {sum(len(s['calls']) for s in log)} calls, errors: {[c['args'].get('due_date') for s in log for c in s['calls'] if c['error']]}"
    )


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1] if len(sys.argv) > 1 else "demo-output")))
