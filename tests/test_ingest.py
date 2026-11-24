import json
import sqlite3

import pytest

from correction_distiller.ingest import (
    InvalidCorrection,
    ingest_file,
    ingest_records,
    parse_record,
)

GOOD = {"quote": "Never text a customer after 8pm.", "date": "2026-03-03", "context": "sending reminders"}


def test_quote_is_kept_exactly_as_given():
    quote = "  Never  text a customer\tafter 8pm.  "
    c = parse_record({**GOOD, "quote": quote})
    assert c.quote == quote


def test_id_is_stable_and_ignores_whitespace_noise():
    a = parse_record(GOOD)
    b = parse_record({**GOOD, "quote": "Never text a  customer after 8pm. "})
    c = parse_record({**GOOD, "date": "2026-03-04"})
    assert a.id == b.id
    assert a.id != c.id
    assert a.id.startswith("c_")


def test_datetime_is_reduced_to_a_date():
    assert parse_record({**GOOD, "date": "2026-03-03T21:40:00Z"}).said_on == "2026-03-03"


def test_said_on_is_accepted_as_an_alias_for_date():
    record = {"quote": GOOD["quote"], "said_on": "2026-03-03", "context": GOOD["context"]}
    assert parse_record(record).said_on == "2026-03-03"


@pytest.mark.parametrize(
    "patch, message",
    [
        ({"quote": ""}, "missing verbatim quote"),
        ({"quote": "   "}, "missing verbatim quote"),
        ({"quote": None}, "missing verbatim quote"),
        ({"quote": "x" * 2001}, "longer than"),
        ({"date": None}, "missing date"),
        ({"date": "last Tuesday"}, "not ISO 8601"),
        ({"date": "03/03/2026"}, "not ISO 8601"),
        ({"context": ""}, "missing context"),
        ({"context": None}, "missing context"),
        ({"agent": 7}, "must be strings"),
    ],
)
def test_incomplete_records_are_refused(patch, message):
    with pytest.raises(InvalidCorrection, match=message):
        parse_record({**GOOD, **patch})


def test_non_object_record_is_refused():
    with pytest.raises(InvalidCorrection, match="not a JSON object"):
        parse_record(["quote"])


def test_ingest_reports_added_duplicates_and_refused(store, clock):
    records = [(1, GOOD), (2, dict(GOOD)), (3, {**GOOD, "date": ""}), (4, {**GOOD, "quote": "Other thing."})]
    report = ingest_records(store, records, clock)
    assert len(report.added) == 2
    assert len(report.duplicates) == 1
    assert report.refused == [(3, "missing date")]
    assert len(store.corrections()) == 2
    assert report.summary() == "added 2, duplicates 1, refused 1"


def test_reingesting_the_same_file_changes_nothing(store, clock, tmp_path, raw):
    path = tmp_path / "corrections.jsonl"
    lines = [json.dumps(r) for r in raw] + ["", "{not json"]
    path.write_text("\n".join(lines), encoding="utf-8")
    first = ingest_file(store, path, clock)
    second = ingest_file(store, path, clock)
    assert len(first.added) == len(raw)
    assert first.refused[0][0] == len(raw) + 2  # line number of the broken line
    assert "not valid JSON" in first.refused[0][1]
    assert second.added == []
    assert len(second.duplicates) == len(raw)


def test_corrections_are_append_only_in_the_database(loaded):
    with pytest.raises(sqlite3.IntegrityError, match="append only"):
        loaded.db.execute("UPDATE corrections SET quote = 'something nicer'")
    with pytest.raises(sqlite3.IntegrityError, match="append only"):
        loaded.db.execute("DELETE FROM corrections")
