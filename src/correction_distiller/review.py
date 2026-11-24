"""The approval queue: the only door into the rule pack.

A human owner approves, rejects or retires. Approval re-checks the evidence
against the stored corrections at the moment of approval, so a proposal that
was fine when drafted but no longer holds cannot slip through.
"""

from __future__ import annotations

from typing import Callable

from .config import Settings
from .evidence import check_citation
from .models import APPROVED, PENDING, REJECTED, RETIRED, Proposal
from .store import Store, utc_now
from .text import normalize_ws


class ReviewError(Exception):
    pass


def _reviewer(store: Store, proposal: Proposal, reviewer: str, settings: Settings) -> str:
    reviewer = normalize_ws(reviewer or "")
    if not reviewer:
        raise ReviewError("a named reviewer is required")
    if reviewer == proposal.drafted_by:
        raise ReviewError("the drafter cannot review its own proposal")
    if settings.owners and reviewer not in settings.owners:
        raise ReviewError(f"{reviewer!r} is not a configured owner")
    return reviewer


def _load(store: Store, proposal_id: str, expected_status: str) -> Proposal:
    proposal = store.get_proposal(proposal_id)
    if proposal is None:
        raise ReviewError(f"no proposal {proposal_id}")
    if proposal.status != expected_status:
        raise ReviewError(f"proposal {proposal_id} is {proposal.status}, expected {expected_status}")
    return proposal


def evidence_problems(store: Store, proposal: Proposal, settings: Settings) -> list[str]:
    """Re-verify every citation of a proposal against the stored corrections."""
    sources = {}
    for citation in proposal.citations:
        correction = store.get_correction(citation.correction_id)
        if correction is not None:
            sources[correction.id] = correction
    problems = []
    for citation in proposal.citations:
        problem = check_citation(citation, sources, settings.min_quote_chars)
        if problem:
            problems.append(problem)
    distinct = len({c.correction_id for c in proposal.citations})
    if distinct < settings.min_citations:
        problems.append(f"cites {distinct} distinct correction(s), needs {settings.min_citations}")
    return problems


def approve(
    store: Store,
    proposal_id: str,
    reviewer: str,
    settings: Settings,
    edited_text: str | None = None,
    note: str = "",
    now: Callable[[], str] = utc_now,
) -> Proposal:
    proposal = _load(store, proposal_id, PENDING)
    reviewer = _reviewer(store, proposal, reviewer, settings)
    problems = evidence_problems(store, proposal, settings)
    if problems:
        raise ReviewError("evidence check failed: " + "; ".join(problems))
    text = None
    if edited_text is not None:
        text = normalize_ws(edited_text)
        if not text:
            raise ReviewError("edited rule text is empty")
        if len(text) > settings.max_rule_chars:
            raise ReviewError(f"edited rule text is longer than {settings.max_rule_chars} characters")
    at = now()
    store.set_decision(proposal_id, APPROVED, reviewer, at, note, text)
    detail = "text edited by reviewer" if text is not None and text != proposal.text else ""
    store.log(at, reviewer, "approve", proposal_id, detail)
    return store.get_proposal(proposal_id)


def reject(
    store: Store,
    proposal_id: str,
    reviewer: str,
    settings: Settings,
    reason: str,
    now: Callable[[], str] = utc_now,
) -> Proposal:
    proposal = _load(store, proposal_id, PENDING)
    reviewer = _reviewer(store, proposal, reviewer, settings)
    reason = normalize_ws(reason or "")
    if not reason:
        raise ReviewError("a rejection needs a reason")
    at = now()
    store.set_decision(proposal_id, REJECTED, reviewer, at, reason)
    store.log(at, reviewer, "reject", proposal_id, reason)
    return store.get_proposal(proposal_id)


def retire(
    store: Store,
    proposal_id: str,
    reviewer: str,
    settings: Settings,
    reason: str,
    now: Callable[[], str] = utc_now,
) -> Proposal:
    """Take an approved rule out of the pack. Its history stays."""
    proposal = _load(store, proposal_id, APPROVED)
    reviewer = _reviewer(store, proposal, reviewer, settings)
    reason = normalize_ws(reason or "")
    if not reason:
        raise ReviewError("retiring a rule needs a reason")
    at = now()
    store.set_decision(proposal_id, RETIRED, reviewer, at, reason)
    store.log(at, reviewer, "retire", proposal_id, reason)
    return store.get_proposal(proposal_id)
