"""The evidence gate: a drafted rule either cites real quotes or it is dropped.

The LLM step is treated as untrusted. For every citation it returns we check
that the quoted text really is a contiguous span of the stored quote of the
correction it points at (whitespace runs are collapsed, nothing else is
forgiven: no case folding, no fuzzy match). One fabricated or paraphrased
citation drops the whole draft, because a drafter that invents evidence for
one line cannot be trusted on the rest of that draft.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Citation, Correction, DraftRule
from .text import normalize_ws

DROP_NO_RULE = "no_rule_text"
DROP_RULE_TOO_LONG = "rule_too_long"
DROP_NO_CITATIONS = "no_citations"
DROP_BAD_CITATION = "citation_not_verbatim"
DROP_TOO_FEW = "too_few_citations"


@dataclass
class EvidenceResult:
    ok: bool
    reason: str = ""
    problems: list[str] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)


def is_verbatim(cited: str, source_quote: str, min_quote_chars: int) -> bool:
    """True if `cited` is a real span of `source_quote`.

    A partial span must be at least `min_quote_chars` long so that a single
    common word cannot stand in as evidence. Citing the whole quote is always
    allowed, however short the quote is.
    """
    cited_n = normalize_ws(cited)
    source_n = normalize_ws(source_quote)
    if not cited_n:
        return False
    if cited_n == source_n:
        return True
    return len(cited_n) >= min_quote_chars and cited_n in source_n


def check_citation(citation: Citation, sources: dict[str, Correction], min_quote_chars: int) -> str | None:
    """Return None if the citation holds, otherwise a human readable problem."""
    source = sources.get(citation.correction_id)
    if source is None:
        return f"{citation.correction_id}: no such correction in this cluster"
    if not is_verbatim(citation.quote, source.quote, min_quote_chars):
        return f"{citation.correction_id}: cited text is not a verbatim span of the source quote"
    return None


def verify_draft(
    draft: DraftRule,
    members: list[Correction],
    min_citations: int = 2,
    min_quote_chars: int = 12,
    max_rule_chars: int = 400,
) -> EvidenceResult:
    """Decide whether a draft may enter the approval queue."""
    text = normalize_ws(draft.text or "")
    if not text:
        return EvidenceResult(False, DROP_NO_RULE, ["draft has no rule text"])
    if len(text) > max_rule_chars:
        return EvidenceResult(False, DROP_RULE_TOO_LONG, [f"rule text is {len(text)} chars, limit {max_rule_chars}"])
    if not draft.citations:
        return EvidenceResult(False, DROP_NO_CITATIONS, ["draft cites no source quotes"])

    sources = {c.id: c for c in members}
    problems: list[str] = []
    valid: dict[str, Citation] = {}
    for citation in draft.citations:
        problem = check_citation(citation, sources, min_quote_chars)
        if problem:
            problems.append(problem)
        else:
            valid.setdefault(citation.correction_id, citation)
    if problems:
        return EvidenceResult(False, DROP_BAD_CITATION, problems)
    if len(valid) < min_citations:
        return EvidenceResult(
            False,
            DROP_TOO_FEW,
            [f"cites {len(valid)} distinct correction(s), needs {min_citations}"],
        )
    ordered = sorted(valid.values(), key=lambda c: (sources[c.correction_id].said_on, c.correction_id))
    return EvidenceResult(True, citations=ordered)
