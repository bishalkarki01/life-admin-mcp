<div align="center">

# Life Admin MCP

**Give your AI assistant a bank statement. It finds every subscription you're paying for, warns you before free trials charge, and tells you what to cancel.**

[![PyPI](https://img.shields.io/pypi/v/life-admin-mcp)](https://pypi.org/project/life-admin-mcp/)
[![CI](https://github.com/bishalkarki01/life-admin-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/bishalkarki01/life-admin-mcp/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Local only](https://img.shields.io/badge/data-stays%20on%20your%20machine-0b5d63)

<img src="https://raw.githubusercontent.com/bishalkarki01/life-admin-mcp/main/docs/demo.gif" alt="Demo: scanning a year of bank transactions finds 12 recurring charges worth $3,406 a year, then asking what to cancel finds $1,067 a year" width="800">

<sub>Real output from a test session. [Watch the full 90-second demo](https://github.com/bishalkarki01/life-admin-mcp/raw/main/docs/demo.mp4).</sub>

</div>

---

People guess they spend about **$86 a month** on subscriptions. When they add it up, the real figure is closer to **$219**, and 42% are paying for something they stopped using ([C+R Research survey, via CNBC](https://www.nbcwashington.com/news/business/money-report/consumers-spend-an-average-133-more-each-month-on-subscriptions-than-they-realize-study-shows/3067565/)).

Life Admin MCP gives Claude, Cursor or any other MCP assistant a memory for this. You just talk to it:

> **You:** Here's my bank export for the last year. Find all my subscriptions.
> **Assistant:** I found 11 recurring charges you aren't tracking, about **$3,407 a year**. Netflix went up from $15.49 to $17.99 in September. PG&E varies from $52 to $129. HelloFresh looks cancelled. Which ones should I track?

> **You:** What's coming up in the next two weeks?
> **Assistant:** $2,254.47 in charges. Rent and the gym on Dec 1, then Netflix, Spotify and your phone. Heads-up: your fridge warranty already expired, and your MacBook's ends Dec 12.

> **You:** What could I cancel?
> **Assistant:** You haven't used the gym since September, and the yoga app and Hulu never. Cancelling those and Max would save **$1,067.64 a year**. You also pay for three streaming services.

## Why you'll like it

- 🔎 **Finds subscriptions you forgot.** Point it at a CSV from your bank or card app. It works with most banks' formats, removes the noise from merchant names, and finds anything that repeats weekly, monthly, quarterly or yearly. It spots price rises, tells variable bills apart from fixed ones, and flags subscriptions that have already stopped. It never stores the statement.
- ⏳ **Catches free trials.** It warns you before a trial becomes a paid subscription.

- 🔒 **Private.** Everything is stored in one SQLite file on your computer. It needs no account or API key, makes no network calls and has no telemetry.
- ⚡ **30-second setup.** One line in your MCP client's config.
- 🧠 **Remembers what you forget.**
  - Recurring charges move to their next date automatically.
  - A bill on the 31st stays at month-end.
  - Your passport reminder can start 6 months early.
- 💸 **Finds savings.** It spots unused subscriptions, overlaps like three streaming services, and price increases since you started tracking.
- 📈 **Forecasts your spending.** It shows how much will go out each month for the next 1–36 months.
- 📅 **Works with your calendar.** It exports a standard `.ics` file with alerts for Google, Apple or Outlook calendars.
- ✅ **Production quality.**
  - 72 tests run on Linux, macOS and Windows, on both MCP SDK v1 and v2.
  - Every input is validated.
  - Schema migrations keep your data safe across upgrades.

## Quick start

You need [uv](https://docs.astral.sh/uv/getting-started/installation/). To install it:
- macOS/Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Windows: `winget install astral-sh.uv`

<details open>
<summary><b>Claude Desktop</b></summary>

Open **Settings → Developer → Edit Config** and add this:

```json
{
  "mcpServers": {
    "life-admin": { "command": "uvx", "args": ["life-admin-mcp"] }
  }
}
```

Restart Claude, then say: *"Track my Netflix, $15.49 monthly, renews on the 2nd."*
</details>

<details>
<summary><b>Claude Code</b></summary>

```bash
claude mcp add life-admin -- uvx life-admin-mcp
```
</details>

<details>
<summary><b>Cursor, VS Code, Windsurf and other clients</b></summary>

Add a stdio server with the command `uvx` and the argument `life-admin-mcp`. The JSON is the same as for Claude Desktop.
</details>

<details>
<summary><b>pip, pipx or Docker</b></summary>

```bash
pipx install life-admin-mcp        # then use the command: life-admin-mcp
docker build -t life-admin-mcp . && docker run -i --rm -v life-admin:/data life-admin-mcp
```
</details>

## Things to ask

| Say this | What happens |
|---|---|
| "Find my subscriptions in ~/Downloads/statement.csv" | Lists recurring charges with yearly costs, price changes and ones that look cancelled, then asks which to track |
| "I started a Duolingo trial, it ends Dec 10 and then costs $12.99 a month" | Tracked as a trial, with a warning before it starts charging |
| "Add my rent, $2,100 on the 1st of every month" | Tracked, with a reminder 7 days ahead |
| "My passport expires 2027-05-02, warn me 6 months before" | Shows up in reminders from November |
| "What's due this week?" | Charges and expiries, with expired items flagged first |
| "How much do I spend on subscriptions?" | Monthly and yearly totals by category and currency |
| "What could I cancel?" | Unused subscriptions and overlaps, with the yearly saving |
| "I used Spotify today" | Usage is recorded, which makes cancel suggestions accurate |
| "Netflix went up to $17.99" | The item is updated |
| "Put it all on my calendar" | An `.ics` file with alerts |
| "Back up my data" | A JSON export you can restore later |
| "What will I spend each month until June?" | A month-by-month forecast |
| "Do my weekly review" | Runs the built-in `weekly_review` prompt |

## Tools

| Tool | Purpose | Kind |
|---|---|---|
| `scan_statement` | Find recurring charges in a bank or card CSV. Nothing is stored. | read-only |
| `add_item` | Track a subscription, free trial, bill, warranty, document or renewal | write |
| `list_items` | List everything, soonest first | read-only |
| `get_upcoming` | Show what's due, what's expired, and heads-ups | read-only |
| `spending_summary` | Show totals by currency and category | read-only |
| `find_savings` | Find unused subscriptions and overlaps | read-only |
| `mark_used` | Record that you used a subscription | write |
| `update_item` | Change any field | write |
| `cancel_item` / `reactivate_item` | Stop or resume tracking (history is kept) | write |
| `delete_item` | Permanently delete an item | **destructive** |
| `forecast` | Show money going out per month | read-only |
| `get_item` | Show full details, including price history | read-only |
| `export_calendar` | Export an RFC 5545 `.ics` file with alarms | read-only |
| `export_data` / `import_data` | Back up or restore as JSON | read / write |

It also includes the resource `life-admin://upcoming` and two prompts: `import_statement` (a guided first-time setup from your bank export) and `weekly_review`. Each tool tells your client whether it only reads, changes data or deletes, so the client can ask before anything risky.

## Configuration

| Setting | Default |
|---|---|
| `--db PATH` or `LIFE_ADMIN_DB` | `~/.life-admin/life_admin.db` |
| `--log-level` or `LIFE_ADMIN_LOG_LEVEL` | `WARNING` (logs go to stderr) |
| `--transport http --port 8765` | Streamable HTTP at `http://127.0.0.1:8765/mcp`, for clients that need a URL |

HTTP mode only listens on your own machine, because it has no login. To bind it to another address you must add `--allow-remote`, and you should only do that behind your own authenticating proxy.

## FAQ

**Does it connect to my bank?**
No, and that's on purpose: nothing leaves your machine. Export a CSV from your bank or card app (most have "Download transactions") and ask your assistant to scan it. The statement is analysed in memory and never stored. Only the items you confirm are saved.

**Which banks work?**
Most CSV exports work. It detects the date, description and amount (or debit/credit) columns, the delimiter, the date format (US, EU or ISO), and whether spending is shown as positive or negative. It also skips header lines some banks put above the data. If yours doesn't work, [open an issue](https://github.com/bishalkarki01/life-admin-mcp/issues/new/choose) with the header row and one made-up line.

**Can I try it without my own data?**
Yes. Download [`sample-statement.csv`](https://raw.githubusercontent.com/bishalkarki01/life-admin-mcp/main/examples/sample-statement.csv), a realistic made-up year of card transactions, then ask your assistant to scan it using the full path where you saved it.

**Will it remind me on its own?**
Your assistant reminds you when you ask. For phone alerts, say "put it all on my calendar" and import the `.ics` file into your calendar app.

**Can I use different currencies?**
Yes. Totals are kept separately for each currency and never converted.

**Where's my data, and how do I back it up?**
It's one file at `~/.life-admin/life_admin.db` (on macOS and Linux, only your user account can read it). To back it up, ask "back up my data", or copy that file.

## Roadmap

- [x] Find subscriptions in a bank CSV export
- [x] Free trials, price history and a spending forecast
- [ ] OFX/QIF statement formats
- [ ] One-click Claude Desktop extension (`.mcpb`)
- [ ] Currency conversion with your own rates
- [ ] Shared household mode

Have an idea? [Open an issue](https://github.com/bishalkarki01/life-admin-mcp/issues/new/choose).

## Contributing

PRs are welcome. See [CONTRIBUTING.md](https://github.com/bishalkarki01/life-admin-mcp/blob/main/CONTRIBUTING.md). The setup takes about a minute:

```bash
pip install -e ".[dev]" && pytest
```

## License

[MIT](https://github.com/bishalkarki01/life-admin-mcp/blob/main/LICENSE). If this saves you money, a ⭐ helps other people find it.

<!-- mcp-name: io.github.bishalkarki01/life-admin-mcp -->
