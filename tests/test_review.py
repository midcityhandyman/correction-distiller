import sqlite3

import pytest

from correction_distiller import review
from correction_distiller.config import Settings
from correction_distiller.llm import MockLLM
from correction_distiller.models import APPROVED, PENDING, REJECTED, RETIRED, Citation, Proposal
from correction_distiller.pipeline import distill
from correction_distiller.review import ReviewError


@pytest.fixture
def queued(loaded, settings, clock):
    distill(loaded, MockLLM(), settings, clock)
    return loaded


def _first(store):
    return store.proposals(PENDING)[0]


def test_approve_records_who_and_when(queued, settings, clock):
    p = review.approve(queued, _first(queued).id, "owner", settings, note="seen it three times", now=clock)
    assert p.status == APPROVED and p.decided_by == "owner"
    assert p.decided_at.startswith("2026-03-16") and p.decision_note == "seen it three times"
    assert queued.audit_log()[-1]["action"] == "approve"


def test_owner_can_edit_the_text_while_approving(queued, settings, clock):
    p = review.approve(queued, _first(queued).id, "owner", settings,
                       edited_text="  Book the site visit  first, then quote. ", now=clock)
    assert p.text == "Book the site visit first, then quote."
    assert queued.audit_log()[-1]["detail"] == "text edited by reviewer"


@pytest.mark.parametrize("edit, message", [("   ", "empty"), ("x" * 401, "longer than")])
def test_bad_edits_are_refused(queued, settings, clock, edit, message):
    with pytest.raises(ReviewError, match=message):
        review.approve(queued, _first(queued).id, "owner", settings, edited_text=edit, now=clock)
    assert _first(queued).status == PENDING


def test_reviewer_must_be_named(queued, settings, clock):
    with pytest.raises(ReviewError, match="named reviewer"):
        review.approve(queued, _first(queued).id, "  ", settings, now=clock)


def test_drafter_cannot_approve_its_own_proposal(queued, settings, clock):
    with pytest.raises(ReviewError, match="own proposal"):
        review.approve(queued, _first(queued).id, "mock-llm", settings, now=clock)
    assert _first(queued).status == PENDING


def test_only_configured_owners_can_decide(queued, clock):
    locked = Settings(owners=("owner",))
    pid = _first(queued).id
    with pytest.raises(ReviewError, match="not a configured owner"):
        review.approve(queued, pid, "some-agent", locked, now=clock)
    with pytest.raises(ReviewError, match="not a configured owner"):
        review.reject(queued, pid, "some-agent", locked, "no", now=clock)
    assert review.approve(queued, pid, "owner", locked, now=clock).status == APPROVED


def test_reject_needs_a_reason(queued, settings, clock):
    pid = _first(queued).id
    with pytest.raises(ReviewError, match="needs a reason"):
        review.reject(queued, pid, "owner", settings, " ", now=clock)
    p = review.reject(queued, pid, "owner", settings, "not a real pattern", now=clock)
    assert p.status == REJECTED and p.decision_note == "not a real pattern"


def test_decisions_are_final(queued, settings, clock):
    pid = _first(queued).id
    review.reject(queued, pid, "owner", settings, "no", now=clock)
    with pytest.raises(ReviewError, match="is rejected, expected pending"):
        review.approve(queued, pid, "owner", settings, now=clock)
    with pytest.raises(ReviewError, match="no proposal"):
        review.approve(queued, "p_missing", "owner", settings, now=clock)


def test_retire_only_applies_to_approved_rules(queued, settings, clock):
    pid = _first(queued).id
    with pytest.raises(ReviewError, match="expected approved"):
        review.retire(queued, pid, "owner", settings, "outdated", now=clock)
    review.approve(queued, pid, "owner", settings, now=clock)
    with pytest.raises(ReviewError, match="needs a reason"):
        review.retire(queued, pid, "owner", settings, "", now=clock)
    assert review.retire(queued, pid, "owner", settings, "policy changed", now=clock).status == RETIRED
    assert queued.approved() == []


def test_approval_rechecks_evidence_against_a_raised_bar(queued, clock):
    two = next(p for p in queued.proposals() if len(p.citations) == 2)
    with pytest.raises(ReviewError, match="evidence check failed"):
        review.approve(queued, two.id, "owner", Settings(min_citations=3), now=clock)
    assert queued.get_proposal(two.id).status == PENDING


def test_approval_fails_if_a_stored_citation_is_not_verbatim(queued, settings, clock):
    p = _first(queued)
    # Simulate a citation that was tampered with in the database.
    queued.db.execute("UPDATE citations SET quote = 'something nobody said, at length' WHERE proposal_id = ?",
                      (p.id,))
    queued.db.commit()
    with pytest.raises(ReviewError, match="not a verbatim span"):
        review.approve(queued, p.id, "owner", settings, now=clock)


# Database level guards: these hold even if someone bypasses review.py.


def test_database_refuses_approval_with_no_citations(queued):
    p = _first(queued)
    queued.db.execute("DELETE FROM citations WHERE proposal_id = ?", (p.id,))
    with pytest.raises(sqlite3.IntegrityError, match="no cited evidence"):
        queued.db.execute("UPDATE proposals SET status = 'approved' WHERE id = ?", (p.id,))


def test_database_refuses_inserting_an_already_approved_proposal(queued):
    with pytest.raises(sqlite3.IntegrityError, match="must enter the queue as pending"):
        queued.db.execute(
            "INSERT INTO proposals (id, status, text, drafted_by, created_at) VALUES ('p_x','approved','t','a','n')"
        )


def test_database_refuses_illegal_transitions_and_late_edits(queued, settings, clock):
    p = _first(queued)
    review.approve(queued, p.id, "owner", settings, now=clock)
    with pytest.raises(sqlite3.IntegrityError, match="illegal status transition"):
        queued.db.execute("UPDATE proposals SET status = 'pending' WHERE id = ?", (p.id,))
    with pytest.raises(sqlite3.IntegrityError, match="only change while the proposal is pending"):
        queued.db.execute("UPDATE proposals SET text = 'quietly different' WHERE id = ?", (p.id,))
    with pytest.raises(sqlite3.IntegrityError, match="cannot remove evidence"):
        queued.db.execute("DELETE FROM citations WHERE proposal_id = ?", (p.id,))


def test_store_refuses_a_proposal_without_citations(store):
    bare = Proposal("p_1", PENDING, "A rule.", "", "", "x", "2026-03-16T09:00:00Z")
    with pytest.raises(ValueError, match="at least one citation"):
        store.add_proposal(bare)


def test_citation_must_point_at_a_stored_correction(store):
    ghost = Proposal("p_1", PENDING, "A rule.", "", "", "x", "2026-03-16T09:00:00Z",
                     citations=[Citation("c_ghost", "never said")])
    with pytest.raises(sqlite3.IntegrityError):
        store.add_proposal(ghost)
    assert store.proposals() == []  # the insert was rolled back as a whole
