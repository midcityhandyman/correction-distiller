from correction_distiller.evidence import (
    DROP_BAD_CITATION,
    DROP_NO_CITATIONS,
    DROP_NO_RULE,
    DROP_RULE_TOO_LONG,
    DROP_TOO_FEW,
    is_verbatim,
    verify_draft,
)
from correction_distiller.models import Citation, DraftRule

SOURCE = "You quoted a price again before booking the site visit. Book the visit first."


def test_whole_quote_is_verbatim_even_when_short():
    assert is_verbatim("Be brief.", "Be brief.", min_quote_chars=12)


def test_long_enough_span_is_verbatim():
    assert is_verbatim("Book the visit first.", SOURCE, 12)


def test_whitespace_runs_are_forgiven_nothing_else():
    assert is_verbatim("Book  the\nvisit first.", SOURCE, 12)
    assert not is_verbatim("book the visit first.", SOURCE, 12)  # case changed
    assert not is_verbatim("Book the visit first!", SOURCE, 12)  # punctuation changed
    assert not is_verbatim("Book the site visit first.", SOURCE, 12)  # word inserted


def test_short_fragment_is_not_evidence():
    assert not is_verbatim("a price", SOURCE, 12)
    assert not is_verbatim("", SOURCE, 12)
    assert not is_verbatim("   ", SOURCE, 12)


def _draft(members, **overrides):
    values = dict(
        text="Book the site visit before quoting a price.",
        citations=[Citation(m.id, m.quote) for m in members],
    )
    values.update(overrides)
    return DraftRule(**values)


def test_honest_draft_passes(corrections):
    members = corrections[:3]
    result = verify_draft(_draft(members), members)
    assert result.ok
    assert [c.correction_id for c in result.citations] == [m.id for m in members]


def test_paraphrased_quote_drops_the_whole_draft(corrections):
    members = corrections[:3]
    citations = [Citation(m.id, m.quote) for m in members]
    citations[1] = Citation(members[1].id, "Please always book the visit before pricing.")
    result = verify_draft(_draft(members, citations=citations), members)
    assert not result.ok
    assert result.reason == DROP_BAD_CITATION
    assert members[1].id in result.problems[0]
    assert result.citations == []


def test_real_quote_attributed_to_the_wrong_correction_is_dropped(corrections):
    members = corrections[:3]
    citations = [Citation(members[0].id, members[1].quote), Citation(members[2].id, members[2].quote)]
    assert verify_draft(_draft(members, citations=citations), members).reason == DROP_BAD_CITATION


def test_citation_from_outside_the_cluster_is_dropped(corrections):
    members = corrections[:3]
    outsider = corrections[3]
    citations = [Citation(m.id, m.quote) for m in members] + [Citation(outsider.id, outsider.quote)]
    result = verify_draft(_draft(members, citations=citations), members)
    assert result.reason == DROP_BAD_CITATION
    assert "no such correction in this cluster" in result.problems[0]


def test_no_citations_is_dropped(corrections):
    members = corrections[:3]
    assert verify_draft(_draft(members, citations=[]), members).reason == DROP_NO_CITATIONS


def test_citing_one_correction_twice_does_not_count_twice(corrections):
    members = corrections[:3]
    citations = [Citation(members[0].id, members[0].quote)] * 2
    result = verify_draft(_draft(members, citations=citations), members, min_citations=2)
    assert result.reason == DROP_TOO_FEW


def test_empty_or_oversized_rule_text_is_dropped(corrections):
    members = corrections[:3]
    assert verify_draft(_draft(members, text="   "), members).reason == DROP_NO_RULE
    assert verify_draft(_draft(members, text="x" * 401), members).reason == DROP_RULE_TOO_LONG
