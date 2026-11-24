"""Ingest human corrections: validate, give each a stable id, store verbatim.

A correction needs three things or it is refused: the verbatim quote, the date
it was said, and the context the agent was working in. Refused records are
reported with a reason; they are never silently fixed up.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Iterable

from .models import Correction
from .store import Store, utc_now
from .text import normalize_ws

MAX_QUOTE_CHARS = 2000


class InvalidCorrection(ValueError):
    pass


@dataclass
class IngestReport:
    added: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    refused: list[tuple[int, str]] = field(default_factory=list)  # (record number, reason)

    def summary(self) -> str:
        return f"added {len(self.added)}, duplicates {len(self.duplicates)}, refused {len(self.refused)}"


def _parse_date(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidCorrection("missing date")
    raw = value.strip()
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
    except ValueError as exc:
        raise InvalidCorrection(f"date {raw!r} is not ISO 8601 (YYYY-MM-DD)") from exc


def correction_id(quote: str, said_on: str, context: str, agent: str) -> str:
    """Content hash, so re-ingesting the same file is a no-op."""
    key = "\x1f".join([normalize_ws(quote), said_on, normalize_ws(context), normalize_ws(agent)])
    return "c_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


def parse_record(record: object) -> Correction:
    """Turn one raw dict into a Correction or raise InvalidCorrection."""
    if not isinstance(record, dict):
        raise InvalidCorrection("record is not a JSON object")
    quote = record.get("quote")
    if not isinstance(quote, str) or not quote.strip():
        raise InvalidCorrection("missing verbatim quote")
    if len(quote) > MAX_QUOTE_CHARS:
        raise InvalidCorrection(f"quote longer than {MAX_QUOTE_CHARS} characters")
    said_on = _parse_date(record.get("date", record.get("said_on")))
    context = record.get("context")
    if not isinstance(context, str) or not context.strip():
        raise InvalidCorrection("missing context")
    agent = record.get("agent") or ""
    source = record.get("source") or ""
    if not isinstance(agent, str) or not isinstance(source, str):
        raise InvalidCorrection("agent and source must be strings")
    context = normalize_ws(context)
    agent = normalize_ws(agent)
    return Correction(
        id=correction_id(quote, said_on, context, agent),
        quote=quote,  # exactly as given
        said_on=said_on,
        context=context,
        agent=agent,
        source=normalize_ws(source),
    )


def read_jsonl(path: str | Path) -> list[tuple[int, object]]:
    """Read a JSON Lines file. Unparseable lines come back as the error string."""
    rows: list[tuple[int, object]] = []
    with open(path, "r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                rows.append((number, json.loads(line)))
            except json.JSONDecodeError as exc:
                rows.append((number, InvalidCorrection(f"not valid JSON: {exc.msg}")))
    return rows


def ingest_records(
    store: Store,
    records: Iterable[tuple[int, object]],
    now: Callable[[], str] = utc_now,
) -> IngestReport:
    report = IngestReport()
    at = now()
    for number, record in records:
        try:
            if isinstance(record, InvalidCorrection):
                raise record
            correction = parse_record(record)
        except InvalidCorrection as exc:
            report.refused.append((number, str(exc)))
            continue
        if store.add_correction(correction, at):
            report.added.append(correction.id)
        else:
            report.duplicates.append(correction.id)
    store.log(at, "ingest", "ingest", "corrections", report.summary())
    return report


def ingest_file(store: Store, path: str | Path, now: Callable[[], str] = utc_now) -> IngestReport:
    return ingest_records(store, read_jsonl(path), now)
