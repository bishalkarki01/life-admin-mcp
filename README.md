<div align="center">

# Life Admin MCP

**Give your AI assistant a bank statement. It finds the recurring charges you're paying for, warns you before free trials charge, and suggests what you could cancel.**

[![PyPI](https://img.shields.io/pypi/v/life-admin-mcp)](https://pypi.org/project/life-admin-mcp/)
[![CI](https://github.com/bishalkarki01/life-admin-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/bishalkarki01/life-admin-mcp/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Local storage](https://img.shields.io/badge/storage-local%20SQLite%2C%20no%20network%20calls-0b5d63)

<img src="https://raw.githubusercontent.com/bishalkarki01/life-admin-mcp/main/docs/demo.gif" alt="Demo: scanning a year of bank transactions finds 12 recurring charges worth $3,406 a year, then asking what to cancel finds $1,067 a year" width="800">

<sub>Real tool output from a demo with a made-up user and made-up data. Your results will differ. [Watch the full 90-second demo](https://github.com/bishalkarki01/life-admin-mcp/raw/main/docs/demo.mp4), or [a real, unedited Claude Code session](https://github.com/bishalkarki01/life-admin-mcp/raw/main/docs/real-session.mp4) (1 minute).</sub>

</div>

---

In a 2022 survey, people guessed they spend about **$86 a month** on subscriptions; asked category by category, the figure was closer to **$219**, and 42% said they'd forgotten a subscription they were still paying for ([C+R Research survey, reported via NBC](https://www.nbcwashington.com/news/business/money-report/consumers-spend-an-average-133-more-each-month-on-subscriptions-than-they-realize-study-shows/3067565/)).

Life Admin MCP gives Claude, Cursor or any other MCP assistant a memory for this. You just talk to it:

> **You:** Here's my bank export for the last year. Find all my subscriptions.
> **Assistant:** I found 11 recurring charges you aren't tracking, about **$3,407 a year**. Netflix went up from $15.49 to $17.99 in September. PG&E varies from $52 to $129. HelloFresh looks cancelled. Which ones should I track?

> **You:** What's coming up in the next two weeks?
> **Assistant:** $2,254.47 in charges. Rent and the gym on Dec 1, then Netflix, Spotify and your phone. Heads-up: your fridge warranty already expired, and your MacBook's ends Dec 12.

> **You:** What could I cancel?
> **Assistant:** You last used the gym in September, and there's no recorded use of the yoga app or Hulu. Cancelling those and Max would save about **$1,067.64 a year**. You also pay for three streaming services.

## Why you'll like it

- 🔎 **Finds subscriptions you forgot.** Point it at a CSV from your bank or card app. It works with most banks' formats, removes the noise from merchant names, and finds anything that repeats weekly, monthly, quarterly or yearly. It spots price rises, tells variable bills apart from fixed ones, and flags subscriptions that have already stopped. It never stores the statement.
- ⏳ **Catches free trials.** It warns you before a trial becomes a paid subscription.

- 🔒 **Local storage, no network.** Everything is stored in one SQLite file on your computer. The server needs no account or API key, makes no network calls and has no telemetry. Your AI assistant does see tool results, so see [Privacy](#privacy).
- ⚡ **30-second setup.** One line in your MCP client's config.
- 🧠 **Remembers what you forget.**
  - Recurring charges move to their next date automatically.
  - A bill on the 31st stays at month-end.
  - Your passport reminder can start 6 months early.
- 💸 **Finds savings.** It spots unused subscriptions, overlaps like three streaming services, and price increases since you started tracking.
- 📈 **Forecasts your spending.** It shows how much will go out each month for the next 1–36 months.
- 📅 **Works with your calendar.** It exports a standard `.ics` file with alerts for Google, Apple or Outlook calendars.
- ✅ **Well tested.**
  - 103 tests run on Linux, macOS and Windows, on both MCP SDK v1 and v2.
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
| "Add my rent, $2,100 on the 1st of every month" | Tracked; shows up in "what's due" from 7 days ahead |
| "My passport expires 2027-05-02, warn me 6 months before" | Shows up in "what's due" from November |
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
| `cancel_item` / `reactivate_item` | Stop or resume tracking. This does not cancel anything with the provider; history is kept | write |
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
No, and that's on purpose: the server never connects to your bank or the internet. Export a CSV from your bank or card app (most have "Download transactions") and ask your assistant to scan it. The statement is analysed in memory and never stored. Only the items you confirm are saved.

**Which banks work?**
Most CSV exports work. It detects the date, description and amount (or debit/credit) columns, the delimiter, the date format (US, EU or ISO), and whether spending is shown as positive or negative. It also skips header lines some banks put above the data. If yours doesn't work, [open an issue](https://github.com/bishalkarki01/life-admin-mcp/issues/new/choose) with the header row and one made-up line. Never post real transactions: issues are public.

**Can I try it without my own data?**
Yes. Download [`sample-statement.csv`](https://raw.githubusercontent.com/bishalkarki01/life-admin-mcp/main/examples/sample-statement.csv), a realistic made-up year of card transactions, then ask your assistant to scan it using the full path where you saved it.

**Will it remind me on its own?**
Your assistant reminds you when you ask. For phone alerts, say "put it all on my calendar" and import the `.ics` file into your calendar app. The file is a snapshot, so export it again after changes. Don't rely on this tool alone for legal, travel or payment deadlines.

**Can I use different currencies?**
Yes. Totals are kept separately for each currency and never converted.

**Where's my data, and how do I back it up?**
It's one file at `~/.life-admin/life_admin.db` (on macOS and Linux, only your user account can read it). To back it up, ask "back up my data", or copy that file.

**How do I delete everything?**
Quit your MCP client and delete the `~/.life-admin/` folder (or the file you set with `--db`, plus any `-wal` and `-shm` files next to it).

## Privacy

The maintainer collects no data. Life Admin MCP has no servers, accounts, analytics or telemetry, and it stores your items in a local SQLite file that you control.

What the tools return (merchant names, amounts, dates, notes) goes into your conversation with your AI assistant. If that assistant uses a cloud model (Claude, ChatGPT, Cursor and so on), its provider processes that text under its own privacy terms. To keep raw statement rows out of the chat, give the assistant a file path instead of pasting the CSV. To keep everything on your machine, use a local model.

## Limitations

Life Admin MCP is a personal organiser, not financial, legal or tax advice. It finds subscriptions by spotting patterns, so it can miss charges, misread amounts or dates, or flag something you still need. "Unused" only means you haven't told it you used something. Reminders appear only when you ask your assistant or import a calendar file. It cannot cancel anything with a provider: check terms, notice periods and fees with the provider before you cancel. It is provided as is, with no warranty (see the [license](https://github.com/bishalkarki01/life-admin-mcp/blob/main/LICENSE)).

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

## Trademarks

Product and company names such as Netflix, Spotify, Hulu, Max, Apple, Adobe, Amazon, YouTube, Claude and Cursor are trademarks of their respective owners. They appear only to describe example transactions and compatible apps. Life Admin MCP is an independent project and is not affiliated with or endorsed by any of them, including Anthropic.

## License

[MIT](https://github.com/bishalkarki01/life-admin-mcp/blob/main/LICENSE). If this saves you money, a ⭐ helps other people find it.

<!-- mcp-name: io.github.bishalkarki01/life-admin-mcp -->
