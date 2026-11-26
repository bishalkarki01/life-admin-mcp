"""End-to-end: launch the real server over stdio and talk to it with the MCP client."""

import os
import re
import sys
from datetime import date, timedelta

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

EXPECTED_TOOLS = {
    "add_item",
    "list_items",
    "get_upcoming",
    "spending_summary",
    "find_savings",
    "mark_used",
    "update_item",
    "cancel_item",
    "reactivate_item",
    "delete_item",
    "export_calendar",
    "export_data",
    "import_data",
    "get_item",
    "forecast",
    "scan_statement",
}


def text(result) -> str:
    return result.content[0].text


def attr(obj, camel: str):
    """Read a field under its SDK v1 (camelCase) or v2 (snake_case) name."""
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", camel).lower()
    return getattr(obj, snake) if hasattr(obj, snake) else getattr(obj, camel)


@pytest.fixture
def params(tmp_path):
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "life_admin_mcp", "--db", str(tmp_path / "e2e.db")],
        env={**os.environ},
    )


async def test_full_flow(params):
    soon = (date.today() + timedelta(days=5)).isoformat()
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()

        tools = {t.name: t for t in (await s.list_tools()).tools}
        assert set(tools) == EXPECTED_TOOLS
        assert attr(tools["delete_item"].annotations, "destructiveHint") is True
        assert attr(tools["list_items"].annotations, "readOnlyHint") is True

        res = await s.call_tool(
            "add_item",
            {"name": "Netflix", "kind": "subscription", "due_date": soon, "amount": 15.49, "cycle": "monthly"},
        )
        assert not attr(res, "isError") and "#1 Netflix" in text(res)

        far = (date.today() + timedelta(days=40)).isoformat()
        await s.call_tool(
            "add_item",
            {"name": "Insurance", "kind": "bill", "due_date": far, "amount": 900, "cycle": "yearly", "remind_days": 45},
        )
        res = await s.call_tool("get_upcoming", {"days": 7})
        due, heads_up = text(res).split("Heads-up")
        assert "Netflix" in due and "Total charges in the next 7 days: 15.49 USD" in due
        assert "Insurance" in heads_up and "900" not in due

        # Validation errors come back as tool errors, not crashes
        res = await s.call_tool("add_item", {"name": "X", "kind": "bill", "due_date": "next friday"})
        assert attr(res, "isError") and text(res).endswith("got 'next friday'")
        res = await s.call_tool("delete_item", {"item_id": 42})
        assert attr(res, "isError") and "No item with id 42" in text(res)

        res = await s.call_tool("export_calendar", {"days": 40})
        assert "BEGIN:VCALENDAR" in text(res)

        prompts = (await s.list_prompts()).prompts
        assert {p.name for p in prompts} == {"weekly_review", "import_statement"}
        resources = (await s.list_resources()).resources
        assert str(resources[0].uri) == "life-admin://upcoming"

        res = await s.call_tool("delete_item", {"item_id": 1})
        assert not attr(res, "isError")


SAMPLE = str(__import__("pathlib").Path(__file__).parent.parent / "examples" / "sample-statement.csv")


async def test_statement_scan_forecast_and_trials(params):
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        res = await s.call_tool("scan_statement", {"csv_path": SAMPLE})
        out = text(res)
        assert not attr(res, "isError")
        assert "Found 12 recurring" in out and "Planet Fitness" in out and "Looks stopped" in out

        res = await s.call_tool("scan_statement", {"csv_path": "/etc/passwd"})
        assert attr(res, "isError") and ".csv" in text(res)
        res = await s.call_tool("scan_statement", {})
        assert attr(res, "isError") and "exactly one" in text(res)

        soon = (date.today() + timedelta(days=10)).isoformat()
        await s.call_tool(
            "add_item", {"name": "Duolingo", "kind": "trial", "due_date": soon, "amount": 12.99, "cycle": "monthly"}
        )
        assert "free trial ends" in text(await s.call_tool("list_items", {}))
        assert "Free trials ending soon" in text(await s.call_tool("find_savings", {}))
        assert "includes trials converting: Duolingo" in text(await s.call_tool("forecast", {"months": 2}))
        await s.call_tool("update_item", {"item_id": 1, "amount": 14.99})
        assert "price history" in text(await s.call_tool("get_item", {"item_id": 1}))


def test_http_refuses_public_bind_without_opt_in():
    import subprocess

    proc = subprocess.run(
        [sys.executable, "-m", "life_admin_mcp", "--transport", "http", "--host", "0.0.0.0"],  # noqa: S104
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode != 0 and "no authentication" in proc.stderr
