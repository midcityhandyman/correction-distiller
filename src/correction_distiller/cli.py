"""Command line interface: `python -m correction_distiller <command>`."""

from __future__ import annotations

import argparse
import sys
from typing import Callable, Sequence

from . import review
from .config import ConfigError, Settings, load_settings
from .ingest import ingest_file
from .llm import LLMClient, LLMError, make_llm
from .models import PENDING, STATUSES, Proposal
from .pipeline import distill
from .rulepack import PackError, load_rule_pack, render_prompt, write_pack
from .store import Store, utc_now


def format_proposal(store: Store, proposal: Proposal, verbose: bool = True) -> str:
    lines = [f"[{proposal.status}] {proposal.id}  {proposal.text}"]
    if not verbose:
        lines[0] += f"  ({len(proposal.citations)} citations)"
        return "\n".join(lines)
    if proposal.scope:
        lines.append(f"  scope:     {proposal.scope}")
    if proposal.rationale:
        lines.append(f"  rationale: {proposal.rationale}")
    lines.append(f"  drafted:   {proposal.created_at} by {proposal.drafted_by}")
    if proposal.decided_by:
        note = f" ({proposal.decision_note})" if proposal.decision_note else ""
        lines.append(f"  decided:   {proposal.decided_at} by {proposal.decided_by}{note}")
    lines.append(f"  evidence ({len(proposal.citations)}):")
    for citation in proposal.citations:
        source = store.get_correction(citation.correction_id)
        lines.append(f'    - {source.said_on} | {source.context} | "{source.quote}" [{source.id}]')
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="correction-distiller",
        description="Distill recurring human corrections into rules a human approves.",
    )
    parser.add_argument("--db", help="path to the SQLite file (default: CD_DB_PATH or .distiller/distiller.db)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ingest", help="ingest corrections from a JSON Lines file")
    p.add_argument("file")

    sub.add_parser("distill", help="cluster corrections and queue proposed rules")

    p = sub.add_parser("queue", help="list proposals")
    p.add_argument("--status", choices=STATUSES + ("all",), default=PENDING)

    p = sub.add_parser("show", help="show one proposal with its evidence")
    p.add_argument("id")

    p = sub.add_parser("approve", help="approve a pending proposal")
    p.add_argument("id")
    p.add_argument("--by", required=True, help="reviewer")
    p.add_argument("--edit", help="replace the drafted rule text")
    p.add_argument("--note", default="")

    p = sub.add_parser("reject", help="reject a pending proposal")
    p.add_argument("id")
    p.add_argument("--by", required=True)
    p.add_argument("--reason", required=True)

    p = sub.add_parser("retire", help="remove an approved rule from the pack")
    p.add_argument("id")
    p.add_argument("--by", required=True)
    p.add_argument("--reason", required=True)

    p = sub.add_parser("export", help="write rulepack.json and rulepack.md")
    p.add_argument("--out", default="rulepack")

    p = sub.add_parser("pack", help="validate a rule pack file and print it as agent instructions")
    p.add_argument("file")
    p.add_argument("--evidence", action="store_true")

    sub.add_parser("dropped", help="list drafts the evidence gate dropped")
    sub.add_parser("log", help="print the audit log")
    return parser


def run(
    argv: Sequence[str],
    settings: Settings | None = None,
    llm: LLMClient | None = None,
    out: Callable[[str], None] = print,
    now: Callable[[], str] = utc_now,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = settings or load_settings(db_path=args.db)
        if args.command == "pack":
            out(render_prompt(load_rule_pack(args.file), with_evidence=args.evidence))
            return 0
        with Store(args.db or settings.db_path) as store:
            return _dispatch(args, store, settings, llm, out, now)
    except UnicodeDecodeError as exc:
        out(f"error: input file is not UTF-8 text ({exc.reason}); save it as UTF-8 and try again")
        return 1
    except (ConfigError, review.ReviewError, PackError, LLMError, OSError) as exc:
        out(f"error: {exc}")
        return 1


def _dispatch(args, store: Store, settings: Settings, llm, out, now) -> int:
    if args.command == "ingest":
        report = ingest_file(store, args.file, now)
        out(report.summary())
        for number, reason in report.refused:
            out(f"  refused record {number}: {reason}")
    elif args.command == "distill":
        report = distill(store, llm or make_llm(settings), settings, now)
        out(report.summary())
        for pid in report.proposed:
            out(f"  proposed {pid}")
        for fingerprint, reason in report.dropped:
            out(f"  dropped {fingerprint}: {reason}")
        for pid, added in report.reinforced:
            out(f"  reinforced {pid}: +{added} citation(s)")
        for pid in report.suppressed:
            out(f"  suppressed: matches decided proposal {pid}")
        for fingerprint, error in report.errors:
            out(f"  llm error on {fingerprint}: {error}")
    elif args.command == "queue":
        status = None if args.status == "all" else args.status
        proposals = store.proposals(status)
        if not proposals:
            out(f"no {args.status} proposals")
        for proposal in proposals:
            out(format_proposal(store, proposal, verbose=False))
    elif args.command == "show":
        proposal = store.get_proposal(args.id)
        if proposal is None:
            out(f"error: no proposal {args.id}")
            return 1
        out(format_proposal(store, proposal))
    elif args.command == "approve":
        proposal = review.approve(store, args.id, args.by, settings, args.edit, args.note, now)
        out(f"approved {proposal.id}: {proposal.text}")
    elif args.command == "reject":
        proposal = review.reject(store, args.id, args.by, settings, args.reason, now)
        out(f"rejected {proposal.id}")
    elif args.command == "retire":
        proposal = review.retire(store, args.id, args.by, settings, args.reason, now)
        out(f"retired {proposal.id}")
    elif args.command == "export":
        pack = write_pack(store, args.out, now)
        out(f"wrote {pack['rule_count']} rule(s) to {args.out} ({pack['checksum'][:19]})")
    elif args.command == "dropped":
        rows = store.dropped()
        if not rows:
            out("nothing dropped")
        for row in rows:
            out(f"{row['fingerprint']}  {row['reason']}  draft: {row['draft_text']!r}")
            for problem in row["problems"]:
                out(f"  - {problem}")
    elif args.command == "log":
        for row in store.audit_log():
            out(f"{row['at']}  {row['actor']:<14} {row['action']:<10} {row['target']}  {row['detail']}".rstrip())
    return 0


def main() -> None:
    sys.exit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
