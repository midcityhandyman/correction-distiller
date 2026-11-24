from correction_distiller.cluster import build_vectors, cluster_corrections, cosine
from correction_distiller.ingest import parse_record
from correction_distiller.text import normalize_ws, stem, tokens


def test_stem_merges_simple_variants():
    assert stem("prices") == stem("price")
    assert stem("quoted") == stem("quotes") == stem("quoting")
    assert stem("dashes") == stem("dash")
    assert stem("texts") == stem("text")
    assert stem("8pm") == "8pm"


def test_tokens_drop_stopwords_and_punctuation():
    assert tokens("Do NOT quote a price, before the visit!") == ["quot", "pric", "visit"]


def test_normalize_ws():
    assert normalize_ws("  a \n b\t\tc ") == "a b c"


def test_cosine_bounds():
    assert cosine({}, {"a": 1.0}) == 0.0
    assert cosine({"a": 1.0}, {"b": 1.0}) == 0.0
    assert abs(cosine({"a": 2.0, "b": 1.0}, {"a": 2.0, "b": 1.0}) - 1.0) < 1e-9


def test_recurring_topics_cluster_and_one_offs_stay_alone(corrections, settings):
    clusters = cluster_corrections(corrections, settings.similarity_threshold)
    sizes = [len(k.members) for k in clusters]
    assert sizes == [3, 2, 2, 1, 1]
    price = clusters[0]
    assert all("visit" in m.quote for m in price.members)
    recurring = [k for k in clusters if k.is_recurring(settings.min_occurrences)]
    assert len(recurring) == 3


def test_every_correction_lands_in_exactly_one_cluster(corrections, settings):
    clusters = cluster_corrections(corrections, settings.similarity_threshold)
    seen = [m.id for k in clusters for m in k.members]
    assert sorted(seen) == sorted(c.id for c in corrections)


def test_clustering_does_not_depend_on_input_order(corrections, settings):
    forward = cluster_corrections(corrections, settings.similarity_threshold)
    backward = cluster_corrections(list(reversed(corrections)), settings.similarity_threshold)
    assert [k.fingerprint for k in forward] == [k.fingerprint for k in backward]


def test_threshold_of_one_only_merges_identical_token_sets(corrections):
    clusters = cluster_corrections(corrections, 1.0)
    assert all(len(k.members) == 1 for k in clusters)


def test_min_distinct_dates_ignores_a_same_day_burst():
    burst = [
        parse_record({"quote": "Never text a customer after 8pm.", "date": "2026-03-03", "context": "a"}),
        parse_record({"quote": "No customer texts after 8pm.", "date": "2026-03-03", "context": "b"}),
    ]
    (cluster,) = cluster_corrections(burst, 0.3)
    assert cluster.is_recurring(2, min_distinct_dates=1)
    assert not cluster.is_recurring(2, min_distinct_dates=2)


def test_fingerprint_changes_when_membership_changes(corrections):
    vectors = build_vectors(corrections)
    assert len(vectors) == len(corrections)
    a = cluster_corrections(corrections[:2], 0.3)[0].fingerprint
    b = cluster_corrections(corrections[:3], 0.3)[0].fingerprint
    assert a != b and a.startswith("k_")


def test_empty_input():
    assert cluster_corrections([], 0.3) == []
