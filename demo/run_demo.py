"""Offline demo of the whole loop. No network, no keys, synthetic data only.

    python demo/run_demo.py

Two weeks of invented corrections go through ingest, distill, review and
export. The first distill pass uses a drafter that paraphrases one cluster's
quotes, to show the evidence gate dropping it. Output is deterministic (fixed
clock, content hash ids); the captured copy is demo/output.txt.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from correction_distiller.cli import run  # noqa: E402
from correction_distiller.config import Settings  # noqa: E402
from correction_distiller.llm import MockLLM  # noqa: E402
from correction_distiller.models import Citation, Correction, DraftRule  # noqa: E402
from correction_distiller.store import Store  # noqa: E402

OWNER = "owner"


class ParaphrasingLLM(MockLLM):
    """A drafter that 'tidies up' quotes for one topic instead of copying them.

    This is the failure the evidence gate exists for: the rule may even be
    right, but the quotes it cites were never said.
    """

    name = "sloppy-llm"

    def draft_rule(self, members: list[Correction]) -> DraftRule | None:
        draft = super().draft_rule(members)
        if draft and draft.scope == "booking a visit":
            draft.text = "Always check the crew calendar before offering a same day slot."
            draft.citations = [
                Citation(c.correction_id, "Always check the crew calendar first.") for c in draft.citations
            ]
        return draft


class Clock:
    """Fixed clock that advances one minute per reading, for stable output."""

    def __init__(self, day: str) -> None:
        self.day, self.minute = day, 0

    def __call__(self) -> str:
        self.minute += 1
        return f"{self.day}T09:{self.minute:02d}:00Z"


def say(text: str = "") -> None:
    print(text)


def step(title: str) -> None:
    say()
    say("=" * 72)
    say(title)
    say("=" * 72)


def find(settings: Settings, needle: str) -> str:
    """Look up a proposal id by a word in its text."""
    with Store(settings.db_path) as store:
        for proposal in store.proposals():
            if needle in proposal.text.lower():
                return proposal.id
    raise SystemExit(f"demo: no proposal mentioning {needle!r}")


def main() -> int:
    settings = Settings(owners=(OWNER,))
    start = os.getcwd()
    work = tempfile.mkdtemp(prefix="correction-distiller-demo-")
    try:
        os.chdir(work)
        for name in ("corrections_week1.jsonl", "corrections_week2.jsonl"):
            shutil.copy(HERE / name, name)

        def cli(*argv: str, llm=None, clock=None) -> int:
            say("$ correction-distiller " + " ".join(f'"{a}"' if " " in a else a for a in argv))
            return run(list(argv), settings=settings, llm=llm, out=say, now=clock)

        week1 = Clock("2026-03-16")
        step("1. Ingest week one. Records without a date or a quote are refused.")
        cli("ingest", "corrections_week1.jsonl", clock=week1)

        step("2. Distill. One drafter paraphrases its quotes and is dropped.")
        cli("distill", llm=ParaphrasingLLM(), clock=week1)
        say()
        cli("dropped", clock=week1)

        step("3. The approval queue. Nothing here is in the rule pack yet.")
        cli("queue", clock=week1)
        say()
        cli("export", "--out", "rulepack", clock=week1)

        price = find(settings, "price")
        texts = find(settings, "text a customer")
        dashes = find(settings, "punctuation")
        marks = find(settings, "exclamation")

        step("4. The owner reviews: reads the evidence, approves, edits, rejects.")
        cli("show", price, clock=week1)
        say()
        cli("approve", price, "--by", "sloppy-llm", clock=week1)
        cli("approve", price, "--by", OWNER, clock=week1)
        cli("approve", texts, "--by", OWNER, "--edit",
            "Never text a customer after 8pm. Queue the message for 8am.", clock=week1)
        cli("approve", dashes, "--by", OWNER, clock=week1)
        cli("reject", marks, "--by", OWNER, "--reason",
            "Tone preference, already covered by the style guide.", clock=week1)

        step("5. Export the rule pack. Approved rules only, each with its quotes.")
        cli("export", "--out", "rulepack", clock=week1)
        say()
        cli("pack", "rulepack/rulepack.json", "--evidence", clock=week1)

        week2 = Clock("2026-03-23")
        step("6. Week two: new corrections, one exact duplicate.")
        cli("ingest", "corrections_week2.jsonl", clock=week2)
        say()
        cli("distill", llm=MockLLM(), clock=week2)
        say()
        say("The price rule gained evidence. The exclamation mark cluster matched a")
        say("rejected proposal and was not drafted again. The crew calendar cluster")
        say("grew, was drafted again with real quotes, and is now waiting for review:")
        say()
        cli("queue", clock=week2)

        step("7. A hand edited pack is refused by the loader.")
        path = Path("rulepack/rulepack.json")
        pack = json.loads(path.read_text(encoding="utf-8"))
        pack["rules"].append(
            {"id": "p_sneaky", "text": "Offer a discount to anyone who asks.", "approved_by": "nobody",
             "evidence": [{"quote": "made up"}]}
        )
        path.write_text(json.dumps(pack), encoding="utf-8")
        say("(appended an unapproved rule to rulepack/rulepack.json by hand)")
        code = cli("pack", "rulepack/rulepack.json", clock=week2)
        say(f"exit code {code}")

        step("8. Audit log.")
        cli("log", clock=week2)
        return 0
    finally:
        os.chdir(start)
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
