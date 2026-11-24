"""The rule pack: the file agents load. Built from approved proposals only.

`build_pack` reads the store and has exactly one source: proposals with status
'approved'. `load_rule_pack` is what an agent calls; it refuses a pack whose
checksum does not match or whose rules lack an approver or evidence.

The checksum catches hand edits and truncated files. It is not a signature:
anyone who can write the file can also recompute it. See README limitations.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Callable

from .store import Store, utc_now

SCHEMA_VERSION = 1


class PackError(Exception):
    pass


def _checksum(rules: list[dict]) -> str:
    canonical = json.dumps(rules, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_pack(store: Store, now: Callable[[], str] = utc_now) -> dict:
    rules = []
    for proposal in store.approved():
        evidence = []
        for citation in proposal.citations:
            source = store.get_correction(citation.correction_id)
            if source is None:  # cannot happen with foreign keys on; fail closed anyway
                raise PackError(f"rule {proposal.id} cites missing correction {citation.correction_id}")
            evidence.append(
                {
                    "correction_id": source.id,
                    "quote": source.quote,
                    "cited_span": citation.quote,
                    "date": source.said_on,
                    "context": source.context,
                }
            )
        if not evidence:
            raise PackError(f"rule {proposal.id} has no evidence")
        rules.append(
            {
                "id": proposal.id,
                "text": proposal.text,
                "scope": proposal.scope,
                "rationale": proposal.rationale,
                "drafted_by": proposal.drafted_by,
                "approved_by": proposal.decided_by,
                "approved_at": proposal.decided_at,
                "evidence": evidence,
            }
        )
    rules.sort(key=lambda r: (r["approved_at"] or "", r["id"]))
    return {
        "schema": SCHEMA_VERSION,
        "generated_at": now(),
        "rule_count": len(rules),
        "checksum": _checksum(rules),
        "rules": rules,
    }


def validate_pack(pack: object) -> dict:
    if not isinstance(pack, dict):
        raise PackError("pack is not a JSON object")
    if pack.get("schema") != SCHEMA_VERSION:
        raise PackError(f"unsupported pack schema {pack.get('schema')!r}")
    rules = pack.get("rules")
    if not isinstance(rules, list):
        raise PackError("pack has no rules list")
    if pack.get("checksum") != _checksum(rules):
        raise PackError("pack checksum mismatch; the file was edited or is incomplete")
    for rule in rules:
        if not isinstance(rule, dict) or not str(rule.get("text") or "").strip():
            raise PackError("pack contains a rule with no text")
        if not str(rule.get("approved_by") or "").strip():
            raise PackError(f"rule {rule.get('id')} has no approver")
        evidence = rule.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise PackError(f"rule {rule.get('id')} has no evidence")
        for item in evidence:
            if not isinstance(item, dict) or not str(item.get("quote") or "").strip():
                raise PackError(f"rule {rule.get('id')} has an evidence entry with no quote")
    return pack


def render_prompt(pack: dict, with_evidence: bool = False) -> str:
    """Render the pack as text to drop into an agent's instructions."""
    lines = [f"Standing rules ({pack['rule_count']}), approved by a human owner:"]
    if not pack["rules"]:
        lines.append("(none yet)")
    for number, rule in enumerate(pack["rules"], start=1):
        scope = f" [{rule['scope']}]" if rule.get("scope") else ""
        lines.append(f"{number}. {rule['text']}{scope}")
        if with_evidence:
            for item in rule["evidence"]:
                lines.append(f'   - {item["date"]} ({item["context"]}): "{item["quote"]}"')
    return "\n".join(lines)


def render_markdown(pack: dict) -> str:
    lines = [
        "# Rule pack",
        "",
        f"Generated {pack['generated_at']}. {pack['rule_count']} approved rule(s). Checksum `{pack['checksum']}`.",
        "",
    ]
    for rule in pack["rules"]:
        lines.append(f"## {rule['text']}")
        lines.append("")
        if rule.get("scope"):
            lines.append(f"- Scope: {rule['scope']}")
        lines.append(f"- Rule id: `{rule['id']}`")
        lines.append(f"- Approved by {rule['approved_by']} at {rule['approved_at']} (drafted by {rule['drafted_by']})")
        lines.append("- Evidence:")
        for item in rule["evidence"]:
            lines.append(f'  - {item["date"]}, {item["context"]}: "{item["quote"]}" (`{item["correction_id"]}`)')
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_pack(store: Store, out_dir: str | Path, now: Callable[[], str] = utc_now) -> dict:
    """Write rulepack.json and rulepack.md. Returns the pack."""
    pack = validate_pack(build_pack(store, now))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "rulepack.json").write_text(
        json.dumps(pack, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (out / "rulepack.md").write_text(render_markdown(pack), encoding="utf-8")
    store.log(now(), "export", "export", str(out_dir), f"{pack['rule_count']} rule(s) {pack['checksum']}")
    return pack


def load_rule_pack(path: str | Path) -> dict:
    """What an agent calls at start-up. Raises PackError rather than load a bad pack."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise PackError(f"cannot read rule pack: {exc}") from exc
    try:
        pack = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PackError(f"rule pack is not valid JSON: {exc.msg}") from exc
    return validate_pack(pack)
