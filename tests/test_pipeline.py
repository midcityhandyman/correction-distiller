from correction_distiller import review
from correction_distiller.evidence import DROP_BAD_CITATION, DROP_NO_CITATIONS
from correction_distiller.ingest import ingest_records
from correction_distiller.llm import LLMError, MockLLM
from correction_distiller.models import APPROVED, PENDING, Citation, DraftRule
from correction_distiller.pipeline import distill
from correction_distiller.rulepack import build_pack


class ScriptedLLM:
    """Test double: returns whatever the given function produces per cluster."""

    name = "scripted-llm"

    def __init__(self, fn):
        self.fn = fn
        self.calls = 0

    def draft_rule(self, members):
        self.calls += 1
        return self.fn(members)


def _by_text(store, needle):
    return next(p for p in store.proposals() if needle in p.text)


def test_distill_queues_one_pending_proposal_per_recurring_cluster(loaded, settings, clock):
    report = distill(loaded, MockLLM(), settings, clock)
    assert report.corrections == 9 and report.clusters == 5 and report.recurring == 3
    assert len(report.proposed) == 3 and not report.dropped
    proposals = loaded.proposals()
    assert {p.status for p in proposals} == {PENDING}
    assert all(len(p.citations) >= settings.min_citations for p in proposals)
    assert all(p.drafted_by == "mock-llm" for p in proposals)


def test_nothing_reaches_the_rule_pack_without_approval(loaded, settings, clock):
    distill(loaded, MockLLM(), settings, clock)
    assert build_pack(loaded, clock)["rules"] == []


def test_every_queued_citation_is_a_verbatim_stored_quote(loaded, settings, clock):
    distill(loaded, MockLLM(), settings, clock)
    for proposal in loaded.proposals():
        for citation in proposal.citations:
            assert citation.quote == loaded.get_correction(citation.correction_id).quote


def test_fabricated_quotes_are_dropped_and_recorded(loaded, settings, clock):
    def fabricate(members):
        return DraftRule("Be nicer.", citations=[Citation(m.id, "The customer is always right.") for m in members])

    report = distill(loaded, ScriptedLLM(fabricate), settings, clock)
    assert report.proposed == []
    assert [reason for _, reason in report.dropped] == [DROP_BAD_CITATION] * 3
    assert loaded.proposals() == []
    dropped = loaded.dropped()
    assert len(dropped) == 3
    assert dropped[0]["draft_text"] == "Be nicer."
    assert dropped[0]["drafted_by"] == "scripted-llm"
    assert len(dropped[0]["problems"]) == len(dropped[0]["member_ids"])


def test_rule_without_citations_is_dropped(loaded, settings, clock):
    report = distill(loaded, ScriptedLLM(lambda m: DraftRule("Just trust me.")), settings, clock)
    assert {reason for _, reason in report.dropped} == {DROP_NO_CITATIONS}
    assert loaded.proposals() == []


def test_dropped_cluster_is_not_redrafted_until_it_changes(loaded, settings, clock):
    bad = ScriptedLLM(lambda m: DraftRule("Just trust me."))
    distill(loaded, bad, settings, clock)
    again = ScriptedLLM(lambda m: DraftRule("Just trust me."))
    report = distill(loaded, again, settings, clock)
    assert again.calls == 0 and report.unchanged == 3
    # A new matching correction changes the cluster, so it gets another try.
    ingest_records(loaded, [(1, {"quote": "Customer texts after 8pm are not ok.", "date": "2026-03-20",
                                 "context": "sending appointment reminders"})], clock)
    report = distill(loaded, MockLLM(), settings, clock)
    assert len(report.proposed) == 1


def test_llm_errors_and_declines_queue_nothing_and_are_retried(loaded, settings, clock):
    def boom(members):
        raise LLMError("endpoint down")

    report = distill(loaded, ScriptedLLM(boom), settings, clock)
    assert len(report.errors) == 3 and loaded.proposals() == [] and loaded.dropped() == []
    report = distill(loaded, ScriptedLLM(lambda m: None), settings, clock)
    assert len(report.declined) == 3 and loaded.proposals() == []
    assert len(distill(loaded, MockLLM(), settings, clock).proposed) == 3


def test_second_run_is_idempotent(loaded, settings, clock):
    distill(loaded, MockLLM(), settings, clock)
    llm = ScriptedLLM(MockLLM().draft_rule)
    report = distill(loaded, llm, settings, clock)
    assert llm.calls == 0
    assert report.proposed == [] and report.unchanged == 3
    assert len(loaded.proposals()) == 3


def test_new_corrections_reinforce_an_approved_rule_without_changing_its_text(loaded, settings, clock):
    distill(loaded, MockLLM(), settings, clock)
    rule = _by_text(loaded, "price")
    review.approve(loaded, rule.id, "owner", settings, now=clock)
    ingest_records(loaded, [(1, {"quote": "Third time: the site visit gets booked before any price is quoted.",
                                 "date": "2026-03-17", "context": "replying to a new estimate request"})], clock)
    report = distill(loaded, MockLLM(), settings, clock)
    assert report.reinforced == [(rule.id, 1)]
    after = loaded.get_proposal(rule.id)
    assert after.status == APPROVED and after.text == rule.text
    assert len(after.citations) == len(rule.citations) + 1
    assert review.evidence_problems(loaded, after, settings) == []


def test_rejected_topic_is_not_proposed_again(loaded, settings, clock):
    distill(loaded, MockLLM(), settings, clock)
    marks = _by_text(loaded, "exclamation")
    review.reject(loaded, marks.id, "owner", settings, "tone preference", now=clock)
    ingest_records(loaded, [(1, {"quote": "Fewer exclamation marks please.", "date": "2026-03-18",
                                 "context": "replying to a review"})], clock)
    report = distill(loaded, MockLLM(), settings, clock)
    assert report.suppressed == [marks.id]
    assert report.proposed == []
    assert len(loaded.get_proposal(marks.id).citations) == 2  # rejected proposals do not collect evidence


def test_min_distinct_dates_setting_filters_bursts(store, clock):
    from correction_distiller.config import Settings

    records = [(i, {"quote": q, "date": "2026-03-03", "context": "reminders"})
               for i, q in enumerate(["Never text a customer after 8pm.", "No customer texts after 8pm."], 1)]
    ingest_records(store, records, clock)
    assert distill(store, MockLLM(), Settings(min_distinct_dates=2), clock).recurring == 0
    assert distill(store, MockLLM(), Settings(), clock).recurring == 1
