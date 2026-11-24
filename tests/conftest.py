from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from correction_distiller.config import Settings  # noqa: E402
from correction_distiller.ingest import ingest_records, parse_record  # noqa: E402
from correction_distiller.store import Store  # noqa: E402

# Synthetic corrections. Three recurring topics and two one-offs.
RAW = [
    {"quote": "Do not quote a price before the site visit is booked.", "date": "2026-03-02",
     "context": "replying to a new estimate request"},
    {"quote": "You quoted a price again before booking the site visit. Book the visit first.",
     "date": "2026-03-05", "context": "replying to a new estimate request"},
    {"quote": "No price quotes until the site visit is on the calendar.", "date": "2026-03-11",
     "context": "replying to a new estimate request"},
    {"quote": "Never text a customer after 8pm.", "date": "2026-03-03",
     "context": "sending appointment reminders"},
    {"quote": "Again, no customer texts after 8pm. Queue them for 8am.", "date": "2026-03-13",
     "context": "sending appointment reminders"},
    {"quote": "Too many exclamation marks in that reply.", "date": "2026-03-06",
     "context": "replying to a review"},
    {"quote": "Drop the exclamation marks, one is plenty.", "date": "2026-03-12",
     "context": "replying to a review"},
    {"quote": "The invoice total for job 1042 was wrong, the tax line was doubled.", "date": "2026-03-04",
     "context": "preparing an invoice"},
    {"quote": "Wrong attachment on the porch repair estimate.", "date": "2026-03-13",
     "context": "sending an estimate"},
]


class Clock:
    def __init__(self) -> None:
        self.n = 0

    def __call__(self) -> str:
        self.n += 1
        return f"2026-03-16T09:{self.n // 60:02d}:{self.n % 60:02d}Z"


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture
def store():
    with Store(":memory:") as s:
        yield s


@pytest.fixture
def raw() -> list[dict]:
    return [dict(r) for r in RAW]


@pytest.fixture
def corrections():
    return [parse_record(r) for r in RAW]


@pytest.fixture
def loaded(store, clock):
    """A store with the synthetic corrections already ingested."""
    ingest_records(store, list(enumerate(RAW, start=1)), clock)
    return store
