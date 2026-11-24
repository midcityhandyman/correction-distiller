"""The distill pass: cluster, draft, gate, enqueue.

For each recurring cluster exactly one of these happens:

* proposed    a new draft passed the evidence gate and is now pending review
* dropped     the draft failed the evidence gate; recorded with the reason
* reinforced  the cluster belongs to a pending or approved rule and has new
              corrections; they are attached as further evidence (the rule
              text is not touched)
* suppressed  the cluster matches a rule a human already rejected or retired,
              so it is not drafted again
* unchanged   the cluster is already fully covered, or was dropped before

Nothing in this module can approve a rule.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Callable

from .cluster import Cluster, cluster_corrections
from .config import Settings
from .evidence import verify_draft
from .llm import LLMClient, LLMError
from .models import APPROVED, PENDING, Citation, Proposal
from .store import Store, utc_now
from .text import normalize_ws

OVERLAP_TO_MATCH = 0.5


@dataclass
class DistillReport:
    corrections: int = 0
    clusters: int = 0
    recurring: int = 0
    proposed: list[str] = field(default_factory=list)
    dropped: list[tuple[str, str]] = field(default_factory=list)  # (fingerprint, reason)
    reinforced: list[tuple[str, int]] = field(default_factory=list)  # (proposal id, new citations)
    suppressed: list[str] = field(default_factory=list)  # proposal ids that blocked a redraft
    declined: list[str] = field(default_factory=list)  # fingerprints the LLM declined
    errors: list[tuple[str, str]] = field(default_factory=list)  # (fingerprint, error)
    unchanged: int = 0

    def summary(self) -> str:
        return (
            f"{self.corrections} corrections, {self.clusters} clusters, {self.recurring} recurring: "
            f"proposed {len(self.proposed)}, dropped {len(self.dropped)}, reinforced {len(self.reinforced)}, "
            f"suppressed {len(self.suppressed)}, declined {len(self.declined)}, errors {len(self.errors)}, "
            f"unchanged {self.unchanged}"
        )


def proposal_id(text: str, correction_ids: list[str]) -> str:
    key = normalize_ws(text) + "\x1f" + "|".join(sorted(correction_ids))
    return "p_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:10]


def _match_existing(cluster: Cluster, existing: list[Proposal]) -> Proposal | None:
    """Find the proposal whose cited corrections mostly sit inside this cluster."""
    member_ids = set(cluster.ids)
    best, best_overlap = None, 0.0
    for proposal in existing:
        cited = {c.correction_id for c in proposal.citations}
        if not cited:
            continue
        overlap = len(cited & member_ids) / len(cited)
        if overlap > best_overlap:
            best, best_overlap = proposal, overlap
    return best if best_overlap >= OVERLAP_TO_MATCH else None


def distill(
    store: Store,
    llm: LLMClient,
    settings: Settings,
    now: Callable[[], str] = utc_now,
) -> DistillReport:
    corrections = store.corrections()
    clusters = cluster_corrections(corrections, settings.similarity_threshold)
    report = DistillReport(corrections=len(corrections), clusters=len(clusters))

    for cluster in clusters:
        if not cluster.is_recurring(settings.min_occurrences, settings.min_distinct_dates):
            continue
        report.recurring += 1
        match = _match_existing(cluster, store.proposals())

        if match is not None:
            if match.status in (PENDING, APPROVED):
                cited = {c.correction_id for c in match.citations}
                added = 0
                for member in cluster.members:
                    if member.id not in cited:
                        # The full stored quote is verbatim by construction.
                        if store.add_citation(match.id, Citation(member.id, member.quote), now()):
                            added += 1
                if added:
                    report.reinforced.append((match.id, added))
                    store.log(now(), "distill", "reinforce", match.id, f"{added} new citation(s)")
                else:
                    report.unchanged += 1
            else:
                report.suppressed.append(match.id)
            continue

        if store.is_dropped(cluster.fingerprint):
            report.unchanged += 1
            continue

        try:
            draft = llm.draft_rule(list(cluster.members))
        except LLMError as exc:
            report.errors.append((cluster.fingerprint, str(exc)))
            store.log(now(), llm.name, "llm_error", cluster.fingerprint, str(exc))
            continue
        if draft is None:
            report.declined.append(cluster.fingerprint)
            store.log(now(), llm.name, "declined", cluster.fingerprint, "")
            continue

        result = verify_draft(
            draft,
            cluster.members,
            min_citations=settings.min_citations,
            min_quote_chars=settings.min_quote_chars,
            max_rule_chars=settings.max_rule_chars,
        )
        if not result.ok:
            store.add_dropped(
                cluster.fingerprint,
                result.reason,
                result.problems,
                cluster.ids,
                normalize_ws(draft.text or ""),
                llm.name,
                now(),
            )
            store.log(now(), "evidence-gate", "drop", cluster.fingerprint, result.reason)
            report.dropped.append((cluster.fingerprint, result.reason))
            continue

        text = normalize_ws(draft.text)
        pid = proposal_id(text, [c.correction_id for c in result.citations])
        if store.get_proposal(pid) is not None:
            report.unchanged += 1
            continue
        store.add_proposal(
            Proposal(
                id=pid,
                status=PENDING,
                text=text,
                rationale=normalize_ws(draft.rationale),
                scope=normalize_ws(draft.scope),
                drafted_by=llm.name,
                created_at=now(),
                citations=result.citations,
            )
        )
        store.log(now(), llm.name, "propose", pid, f"{len(result.citations)} citation(s)")
        report.proposed.append(pid)

    return report
