"""correction-distiller: turn recurring human corrections into approved agent rules.

Pipeline: ingest corrections -> cluster recurring ones -> draft a rule per
cluster (LLM behind an interface) -> evidence gate -> approval queue -> rule pack.
"""

from .models import Citation, Correction, DraftRule, Proposal
from .rulepack import load_rule_pack, render_prompt

__all__ = [
    "Citation",
    "Correction",
    "DraftRule",
    "Proposal",
    "load_rule_pack",
    "render_prompt",
]

__version__ = "0.1.0"
