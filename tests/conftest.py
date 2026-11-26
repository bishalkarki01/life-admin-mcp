from datetime import date

import pytest

from life_admin_mcp.store import Store

TODAY = date(2026, 1, 15)


class Clock:
    def __init__(self, d: date) -> None:
        self.d = d

    def __call__(self) -> date:
        return self.d


@pytest.fixture
def clock() -> Clock:
    return Clock(TODAY)


@pytest.fixture
def store(tmp_path, clock) -> Store:
    return Store(tmp_path / "test.db", today=clock)
