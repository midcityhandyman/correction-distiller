"""Plain data types shared by every stage of the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field

PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
RETIRED = "retired"
STATUSES = (PENDING, APPROVED, REJECTED, RETIRED)


@dataclass(frozen=True)
class Correction:
    """One thing a human said to correct an agent.

    `quote` is stored exactly as it was given. Nothing downstream is allowed
    to rewrite it; rules cite it, they never replace it.
    """

    id: str
    quote: str
    said_on: str  # ISO date, YYYY-MM-DD
    context: str  # what the agent was doing when it was corrected
    agent: str = ""
    source: str = ""


@dataclass(frozen=True)
class Citation:
    """A pointer from a rule to a span of a correction's verbatim quote."""

    correction_id: str
    quote: str


@dataclass
class DraftRule:
    """What the LLM step returns for one cluster. Untrusted until verified."""

    text: str
    rationale: str = ""
    scope: str = ""
    citations: list[Citation] = field(default_factory=list)


@dataclass
class Proposal:
    """A drafted rule sitting in (or decided out of) the approval queue."""

    id: str
    status: str
    text: str
    rationale: str
    scope: str
    drafted_by: str
    created_at: str
    decided_at: str | None = None
    decided_by: str | None = None
    decision_note: str | None = None
    citations: list[Citation] = field(default_factory=list)
