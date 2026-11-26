# Contributing

Thanks for helping! Bug reports, ideas and pull requests are all welcome.

By submitting a contribution, you agree that it is licensed under the project's [MIT License](LICENSE) and that you have the right to submit it.

**Issues are public.** Never post real bank or card data, account or card numbers, names, addresses or document numbers. For a CSV problem, share only the header row and one made-up line.

## Quick start

```bash
git clone https://github.com/bishalkarki01/life-admin-mcp && cd life-admin-mcp
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest
```

To try your changes in a real client, point it at your checkout:

```bash
claude mcp add life-admin-dev -- python -m life_admin_mcp --db /tmp/dev.db
```

## Before you open a pull request

```bash
ruff check . && ruff format . && mypy src && pytest
```

- Put new business logic in `store.py`, with tests in `tests/test_store.py`. Keep `server.py` as a thin layer that formats the output.
- A new tool also needs to be added to the integration test, the README tool table and the CHANGELOG.
- Changing the database schema? Bump `SCHEMA_VERSION` and add a migration step in `Store._migrate`. Never break existing databases.

## Good first issues

Look for the [`good first issue`](https://github.com/bishalkarki01/life-admin-mcp/labels/good%20first%20issue) label. Ideas on the roadmap:

- Support more statement formats (OFX, QIF) and more bank CSV quirks
- Better merchant-name cleanup (send examples!)
- Currency conversion with user-supplied rates
- A Claude Desktop one-click bundle (`.mcpb`)

## Releasing (maintainers)

1. Bump the version in `src/life_admin_mcp/__init__.py` and in both places in `server.json`.
2. Add a `## x.y.z` section to `CHANGELOG.md`.
3. `git tag vX.Y.Z && git push --tags`

CI then publishes to PyPI, the MCP Registry and GitHub Releases.
