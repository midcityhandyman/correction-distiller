"""Small text helpers: whitespace normalisation, tokenising, a crude stemmer.

Kept dependency free on purpose. The stemmer is deliberately simple; it only
has to make "prices" and "price" or "texting" and "texts" land on one token.
"""

from __future__ import annotations

import re

STOPWORDS = frozenset(
    """
    a an and are as at be been but by can did do does for from had has have i if in
    into is it its just me my of on or our please so that the their them then there
    these they this to up us was we were what when which who will with you your
    again always already also any before after until not no never dont don t stop
    should must need needs said told keep still ever every all one more than
    """.split()
)

_WORD = re.compile(r"[a-z0-9]+")


def normalize_ws(value: str) -> str:
    """Collapse every run of whitespace to one space and trim the ends."""
    return " ".join(value.split())


def stem(token: str) -> str:
    """Strip a few common English suffixes. Not linguistically correct."""
    if token.isdigit() or len(token) <= 3:
        return token
    for suffix in ("ing", "ed"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            token = token[: -len(suffix)]
            break
    else:
        if token.endswith("es") and token[:-2].endswith(("sh", "ch", "x", "ss")):
            token = token[:-2]
        elif token.endswith("s") and not token.endswith("ss"):
            token = token[:-1]
    if token.endswith("e") and len(token) >= 4:
        token = token[:-1]
    return token


def tokens(value: str) -> list[str]:
    """Lowercase, split on non alphanumerics, drop stopwords, stem."""
    out = []
    for raw in _WORD.findall(value.lower()):
        if raw in STOPWORDS:
            continue
        out.append(stem(raw))
    return out
