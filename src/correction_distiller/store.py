"""SQLite store: corrections, proposals, citations, dropped drafts, audit log.

Two invariants live in the database itself, not only in Python:

* corrections are append only (no UPDATE, no DELETE), so a quote that a rule
  cites can never be edited after the fact;
* a proposal cannot be moved to 'approved' while it has zero citations, a
  citation cannot be removed from an approved proposal, and no citation can be
  rewritten in place (citations are only ever added).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import APPROVED, PENDING, Citation, Correction, Proposal

SCHEMA = """
CREATE TABLE IF NOT EXISTS corrections (
    id          TEXT PRIMARY KEY,
    quote       TEXT NOT NULL CHECK (length(trim(quote)) > 0),
    said_on     TEXT NOT NULL,
    context     TEXT NOT NULL CHECK (length(trim(context)) > 0),
    agent       TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT '',
    ingested_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS proposals (
    id            TEXT PRIMARY KEY,
    status        TEXT NOT NULL CHECK (status IN ('pending','approved','rejected','retired')),
    text          TEXT NOT NULL CHECK (length(trim(text)) > 0),
    rationale     TEXT NOT NULL DEFAULT '',
    scope         TEXT NOT NULL DEFAULT '',
    drafted_by    TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    decided_at    TEXT,
    decided_by    TEXT,
    decision_note TEXT
);

CREATE TABLE IF NOT EXISTS citations (
    proposal_id   TEXT NOT NULL REFERENCES proposals(id),
    correction_id TEXT NOT NULL REFERENCES corrections(id),
    quote         TEXT NOT NULL CHECK (length(trim(quote)) > 0),
    added_at      TEXT NOT NULL,
    PRIMARY KEY (proposal_id, correction_id)
);

CREATE TABLE IF NOT EXISTS dropped (
    fingerprint TEXT PRIMARY KEY,
    reason      TEXT NOT NULL,
    problems    TEXT NOT NULL,
    member_ids  TEXT NOT NULL,
    draft_text  TEXT NOT NULL DEFAULT '',
    drafted_by  TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    seq    INTEGER PRIMARY KEY AUTOINCREMENT,
    at     TEXT NOT NULL,
    actor  TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);

CREATE TRIGGER IF NOT EXISTS corrections_no_update BEFORE UPDATE ON corrections
BEGIN SELECT RAISE(ABORT, 'corrections are append only'); END;

CREATE TRIGGER IF NOT EXISTS corrections_no_delete BEFORE DELETE ON corrections
BEGIN SELECT RAISE(ABORT, 'corrections are append only'); END;

CREATE TRIGGER IF NOT EXISTS approve_needs_evidence BEFORE UPDATE OF status ON proposals
WHEN NEW.status = 'approved'
     AND (SELECT COUNT(*) FROM citations WHERE proposal_id = NEW.id) = 0
BEGIN SELECT RAISE(ABORT, 'cannot approve a rule with no cited evidence'); END;

CREATE TRIGGER IF NOT EXISTS approved_keeps_evidence BEFORE DELETE ON citations
WHEN (SELECT status FROM proposals WHERE id = OLD.proposal_id) = 'approved'
BEGIN SELECT RAISE(ABORT, 'cannot remove evidence from an approved rule'); END;

CREATE TRIGGER IF NOT EXISTS citations_no_update BEFORE UPDATE ON citations
BEGIN SELECT RAISE(ABORT, 'citations cannot be rewritten'); END;

CREATE TRIGGER IF NOT EXISTS status_transitions BEFORE UPDATE OF status ON proposals
WHEN NOT ((OLD.status = 'pending' AND NEW.status IN ('approved', 'rejected'))
          OR (OLD.status = 'approved' AND NEW.status = 'retired'))
BEGIN SELECT RAISE(ABORT, 'illegal status transition'); END;

CREATE TRIGGER IF NOT EXISTS decided_text_is_frozen BEFORE UPDATE OF text ON proposals
WHEN OLD.status <> 'pending'
BEGIN SELECT RAISE(ABORT, 'rule text can only change while the proposal is pending'); END;

CREATE TRIGGER IF NOT EXISTS no_direct_approved_insert BEFORE INSERT ON proposals
WHEN NEW.status <> 'pending'
BEGIN SELECT RAISE(ABORT, 'proposals must enter the queue as pending'); END;
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Store:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # corrections -----------------------------------------------------------

    def add_correction(self, c: Correction, ingested_at: str) -> bool:
        """Insert a correction. Returns False if it was already stored."""
        cur = self.db.execute(
            "INSERT OR IGNORE INTO corrections (id, quote, said_on, context, agent, source, ingested_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (c.id, c.quote, c.said_on, c.context, c.agent, c.source, ingested_at),
        )
        self.db.commit()
        return cur.rowcount == 1

    @staticmethod
    def _correction(row: sqlite3.Row) -> Correction:
        return Correction(row["id"], row["quote"], row["said_on"], row["context"], row["agent"], row["source"])

    def corrections(self) -> list[Correction]:
        rows = self.db.execute("SELECT * FROM corrections ORDER BY said_on, id").fetchall()
        return [self._correction(r) for r in rows]

    def get_correction(self, correction_id: str) -> Correction | None:
        row = self.db.execute("SELECT * FROM corrections WHERE id = ?", (correction_id,)).fetchone()
        return self._correction(row) if row else None

    # proposals -------------------------------------------------------------

    def add_proposal(self, p: Proposal) -> None:
        """Insert a pending proposal together with its citations, atomically."""
        if not p.citations:
            raise ValueError("a proposal must carry at least one citation")
        with self.db:
            self.db.execute(
                "INSERT INTO proposals (id, status, text, rationale, scope, drafted_by, created_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (p.id, PENDING, p.text, p.rationale, p.scope, p.drafted_by, p.created_at),
            )
            self.db.executemany(
                "INSERT INTO citations (proposal_id, correction_id, quote, added_at) VALUES (?,?,?,?)",
                [(p.id, c.correction_id, c.quote, p.created_at) for c in p.citations],
            )

    def add_citation(self, proposal_id: str, citation: Citation, added_at: str) -> bool:
        cur = self.db.execute(
            "INSERT OR IGNORE INTO citations (proposal_id, correction_id, quote, added_at) VALUES (?,?,?,?)",
            (proposal_id, citation.correction_id, citation.quote, added_at),
        )
        self.db.commit()
        return cur.rowcount == 1

    def _citations(self, proposal_id: str) -> list[Citation]:
        rows = self.db.execute(
            "SELECT ci.correction_id, ci.quote FROM citations ci JOIN corrections co ON co.id = ci.correction_id"
            " WHERE ci.proposal_id = ? ORDER BY co.said_on, co.id",
            (proposal_id,),
        ).fetchall()
        return [Citation(r["correction_id"], r["quote"]) for r in rows]

    def _proposal(self, row: sqlite3.Row) -> Proposal:
        return Proposal(
            id=row["id"],
            status=row["status"],
            text=row["text"],
            rationale=row["rationale"],
            scope=row["scope"],
            drafted_by=row["drafted_by"],
            created_at=row["created_at"],
            decided_at=row["decided_at"],
            decided_by=row["decided_by"],
            decision_note=row["decision_note"],
            citations=self._citations(row["id"]),
        )

    def get_proposal(self, proposal_id: str) -> Proposal | None:
        row = self.db.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
        return self._proposal(row) if row else None

    def proposals(self, status: str | None = None) -> list[Proposal]:
        if status is None:
            rows = self.db.execute("SELECT * FROM proposals ORDER BY created_at, id").fetchall()
        else:
            rows = self.db.execute(
                "SELECT * FROM proposals WHERE status = ? ORDER BY created_at, id", (status,)
            ).fetchall()
        return [self._proposal(r) for r in rows]

    def set_decision(
        self,
        proposal_id: str,
        status: str,
        decided_by: str,
        decided_at: str,
        note: str = "",
        text: str | None = None,
    ) -> None:
        with self.db:
            if text is not None:
                self.db.execute("UPDATE proposals SET text = ? WHERE id = ?", (text, proposal_id))
            self.db.execute(
                "UPDATE proposals SET status = ?, decided_by = ?, decided_at = ?, decision_note = ? WHERE id = ?",
                (status, decided_by, decided_at, note, proposal_id),
            )

    def approved(self) -> list[Proposal]:
        return self.proposals(APPROVED)

    # dropped drafts --------------------------------------------------------

    def add_dropped(
        self,
        fingerprint: str,
        reason: str,
        problems: list[str],
        member_ids: list[str],
        draft_text: str,
        drafted_by: str,
        created_at: str,
    ) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO dropped (fingerprint, reason, problems, member_ids, draft_text, drafted_by,"
            " created_at) VALUES (?,?,?,?,?,?,?)",
            (fingerprint, reason, json.dumps(problems), json.dumps(member_ids), draft_text, drafted_by, created_at),
        )
        self.db.commit()

    def dropped(self) -> list[dict]:
        rows = self.db.execute("SELECT * FROM dropped ORDER BY created_at, fingerprint").fetchall()
        out = []
        for r in rows:
            item = dict(r)
            item["problems"] = json.loads(item["problems"])
            item["member_ids"] = json.loads(item["member_ids"])
            out.append(item)
        return out

    def is_dropped(self, fingerprint: str) -> bool:
        row = self.db.execute("SELECT 1 FROM dropped WHERE fingerprint = ?", (fingerprint,)).fetchone()
        return row is not None

    # audit -----------------------------------------------------------------

    def log(self, at: str, actor: str, action: str, target: str, detail: str = "") -> None:
        self.db.execute(
            "INSERT INTO audit_log (at, actor, action, target, detail) VALUES (?,?,?,?,?)",
            (at, actor, action, target, detail),
        )
        self.db.commit()

    def audit_log(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM audit_log ORDER BY seq").fetchall()]
