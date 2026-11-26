# Security policy

## Reporting a vulnerability

Please **don't open a public issue**. Instead, use GitHub's private reporting:
[Report a vulnerability](https://github.com/bishalkarki01/life-admin-mcp/security/advisories/new).

You'll get a reply within 7 days. Once a fix ships, you'll be credited unless you'd rather not be.

## Supported versions

Security fixes go into the latest release.

## Design

- **No network access.** The server makes no outbound connections and collects no telemetry.
- **Narrow file access.** It writes only to its own SQLite database (`~/.life-admin/` by default), which it creates so only your user can open it. The only other files it reads are statements passed to `scan_statement` as `csv_path`: any `.csv`, `.tsv` or `.txt` file under 5 MB that your account can read. Keep your client's tool-approval prompts on to see which file it reads.
- **Statements stay in memory.** `scan_statement` analyses a statement in memory and never stores it. With `csv_path` it opens only `.csv`, `.tsv` or `.txt` files under 5 MB.
- **HTTP stays local.** HTTP mode binds to 127.0.0.1 and refuses other addresses unless you pass `--allow-remote`, because it has no authentication.
- **Validated input.** Every tool input is checked for type, range and length, and SQL queries use bound parameters.
- **Clear risk labels.** Tools are annotated as read-only, write or destructive, so MCP clients can ask before risky calls.
