"""Group corrections that say the same thing.

Method: IDF weighted bag of stemmed tokens, cosine similarity, greedy single
pass assignment to the best matching cluster centroid. It is deterministic
(input is sorted by date then id) and needs no model or network. It is also
lexical only: two corrections that mean the same thing in entirely different
words will not meet. See the README limitations.
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from dataclasses import dataclass, field

from .models import Correction
from .text import tokens

Vector = dict[str, float]


def build_vectors(corrections: list[Correction]) -> dict[str, Vector]:
    """One IDF weighted vector per correction, keyed by correction id."""
    token_sets = {c.id: set(tokens(c.quote)) for c in corrections}
    df: Counter[str] = Counter()
    for token_set in token_sets.values():
        df.update(token_set)
    n = len(corrections)
    vectors: dict[str, Vector] = {}
    for cid, token_set in token_sets.items():
        vectors[cid] = {t: math.log((n + 1) / (df[t] + 1)) + 1.0 for t in token_set}
    return vectors


def cosine(a: Vector, b: Vector) -> float:
    if not a or not b:
        return 0.0
    if len(a) > len(b):
        a, b = b, a
    dot = sum(weight * b.get(token, 0.0) for token, weight in a.items())
    if dot == 0.0:
        return 0.0
    norm_a = math.sqrt(sum(w * w for w in a.values()))
    norm_b = math.sqrt(sum(w * w for w in b.values()))
    return dot / (norm_a * norm_b)


@dataclass
class Cluster:
    members: list[Correction] = field(default_factory=list)
    centroid: Vector = field(default_factory=dict)

    @property
    def ids(self) -> list[str]:
        return sorted(c.id for c in self.members)

    @property
    def fingerprint(self) -> str:
        """Stable identity of this exact set of corrections."""
        return "k_" + hashlib.sha256("|".join(self.ids).encode("utf-8")).hexdigest()[:12]

    @property
    def distinct_dates(self) -> int:
        return len({c.said_on for c in self.members})

    def add(self, correction: Correction, vector: Vector) -> None:
        self.members.append(correction)
        for token, weight in vector.items():
            self.centroid[token] = self.centroid.get(token, 0.0) + weight

    def is_recurring(self, min_occurrences: int, min_distinct_dates: int = 1) -> bool:
        return len(self.members) >= min_occurrences and self.distinct_dates >= min_distinct_dates


def cluster_corrections(corrections: list[Correction], threshold: float) -> list[Cluster]:
    """Greedy clustering. Returns clusters largest first, ties by earliest date."""
    ordered = sorted(corrections, key=lambda c: (c.said_on, c.id))
    vectors = build_vectors(ordered)
    clusters: list[Cluster] = []
    for correction in ordered:
        vector = vectors[correction.id]
        best, best_score = None, 0.0
        for cluster in clusters:
            score = cosine(vector, cluster.centroid)
            if score > best_score:
                best, best_score = cluster, score
        if best is not None and best_score >= threshold:
            best.add(correction, vector)
        else:
            fresh = Cluster()
            fresh.add(correction, vector)
            clusters.append(fresh)
    clusters.sort(key=lambda k: (-len(k.members), k.members[0].said_on, k.members[0].id))
    return clusters
