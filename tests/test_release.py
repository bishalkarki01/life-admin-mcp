"""Release hygiene: versions agree and the registry ownership marker is present."""

import json
from pathlib import Path

from life_admin_mcp import __version__

ROOT = Path(__file__).parent.parent


def test_server_json_matches_package_version():
    server = json.loads((ROOT / "server.json").read_text())
    assert server["version"] == __version__
    assert all(p["version"] == __version__ for p in server["packages"])


def test_readme_has_registry_ownership_marker():
    server = json.loads((ROOT / "server.json").read_text())
    assert f"mcp-name: {server['name']}" in (ROOT / "README.md").read_text()


def test_changelog_mentions_current_version():
    assert f"## {__version__}" in (ROOT / "CHANGELOG.md").read_text()
