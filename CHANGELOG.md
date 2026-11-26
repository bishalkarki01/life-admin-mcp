# Changelog

## 1.0.1 — 2026-11-25

### Fixed
- Calendar export labelled every later charge of a free trial as "free trial ends". The trial's end date now appears once, and the charges that follow read "due … (if the trial wasn't cancelled)". Found by a real end-to-end session with Claude Code.

### Added
- `docs/real-session.mp4` and `.gif`: a real, unedited Claude Code session using the published package on the made-up sample statement, rendered by `demo/render_session.py`.

## 1.0.0 — 2026-11-25

First stable release.

### Added
- **Statement scanning (`scan_statement`).** Finds recurring charges in a bank or card CSV:
  - Handles most banks' formats: delimiter, header detection, preamble lines, US/EU/ISO dates, debit/credit columns, either sign convention, European number formats.
  - Cleans up merchant names and detects weekly, monthly, quarterly and yearly cadences, with a confidence level.
  - Reports price rises and when they started, gives variable bills a range instead of a price, and flags subscriptions that have stopped.
  - Compares results with tracked items. Nothing from the statement is stored.
- **Free trials** (`kind="trial"`): warns before a trial starts charging. Trials stay out of spending totals but are included in the forecast. Convert a kept trial with `update_item(kind="subscription")`.
- **Price history:** every price change is recorded. `get_item` shows the history, and `spending_summary` lists increases since you started tracking.
- **`forecast`:** money going out per month, for 1–36 months.
- **`import_statement` prompt:** a guided first-time setup from a bank export.
- **Streamable HTTP transport** (`--transport http`): bound to 127.0.0.1. It refuses public addresses unless you pass `--allow-remote`.
- `examples/sample-statement.csv`: a realistic made-up year of transactions for trying the scanner.
- Installable package with a `life-admin-mcp` command (`pip`, `pipx` and `uvx` all work). Options: `--db`, `--log-level` and `--version`.
- New tools: `reactivate_item`, `export_data` and `import_data` (JSON backup and restore).
- Tool annotations that tell clients which tools only read, which change data, and which delete.
- A `weekly_review` prompt and a `life-admin://upcoming` resource.
- Works with MCP Python SDK v1 and v2.
- 103 tests, including end-to-end stdio and HTTP-safety tests. CI runs them on Linux, macOS and Windows, Python 3.10 and 3.13, and both SDK versions.
- Pushing a version tag publishes to PyPI, the official MCP Registry (`io.github.bishalkarki01/life-admin-mcp`) and GitHub Releases.

### Fixed (found while testing on a realistic session)
- `get_upcoming` totalled charges outside the requested window. Items that appear early because of their reminder window are now listed under a separate "heads-up" section and left out of the total.
- Error messages lost their final quote character.

### Changed
- Amounts are stored as integer cents, so totals no longer pick up floating-point rounding errors.
- Monthly and yearly items keep their original day: a bill on the 31st goes Jan 31 → Feb 28 → Mar 31 instead of drifting to the 28th.
- Calendar export now follows RFC 5545: it includes the required `DTSTAMP`, escapes commas and semicolons, and folds long lines.
- Validation errors, such as a bad date, currency or id, now reach the assistant as readable messages. Before, SDK v2 showed only a generic "Error executing tool".

### Safety
- Schema version 2, with step-by-step migrations from the prototype and v1 databases.
- `scan_statement` reads local files only if they end in `.csv`, `.tsv` or `.txt` and are under 5 MB.
- Every input is checked for type, range and length. Database `CHECK` constraints back these checks up.
- An active item with the same name and kind can't be added twice.
- The database uses WAL mode with a busy timeout and rolls back writes that fail. The data folder is created with `0700` permissions.
- The schema is versioned. Prototype databases are migrated automatically, and a database created by a newer version is refused instead of being damaged.
- Logs go to stderr only, so they never corrupt the MCP stream on stdout.
