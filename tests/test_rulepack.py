import json

import pytest

from correction_distiller import review
from correction_distiller.llm import MockLLM
from correction_distiller.pipeline import distill
from correction_distiller.rulepack import (
    PackError,
    build_pack,
    load_rule_pack,
    render_markdown,
    render_prompt,
    write_pack,
)


@pytest.fixture
def decided(loaded, settings, clock):
    """One approved, one rejected, one left pending."""
    distill(loaded, MockLLM(), settings, clock)
    by = {("price" in p.text, "text a customer" in p.text): p for p in loaded.proposals()}
    review.approve(loaded, by[(True, False)].id, "owner", settings, now=clock)
    review.reject(loaded, by[(False, True)].id, "owner", settings, "testing", now=clock)
    return loaded


def test_pack_contains_only_approved_rules(decided, clock):
    pack = build_pack(decided, clock)
    assert pack["rule_count"] == 1
    (rule,) = pack["rules"]
    assert "price" in rule["text"]
    assert rule["approved_by"] == "owner" and rule["drafted_by"] == "mock-llm"


def test_every_rule_carries_its_verbatim_quotes_dates_and_context(decided, clock):
    (rule,) = build_pack(decided, clock)["rules"]
    assert len(rule["evidence"]) == 3
    for item in rule["evidence"]:
        source = decided.get_correction(item["correction_id"])
        assert item["quote"] == source.quote
        assert item["date"] == source.said_on and item["context"] == source.context


def test_retired_rules_leave_the_pack(decided, settings, clock):
    rule_id = build_pack(decided, clock)["rules"][0]["id"]
    review.retire(decided, rule_id, "owner", settings, "policy changed", now=clock)
    assert build_pack(decided, clock)["rules"] == []


def test_write_then_load_round_trip(decided, clock, tmp_path):
    written = write_pack(decided, tmp_path / "out", clock)
    loaded_pack = load_rule_pack(tmp_path / "out" / "rulepack.json")
    assert loaded_pack == written
    md = (tmp_path / "out" / "rulepack.md").read_text(encoding="utf-8")
    assert "Approved by owner" in md and written["rules"][0]["evidence"][0]["quote"] in md
    assert md == render_markdown(written)


def test_checksum_ignores_generation_time_but_not_content(decided, settings, clock):
    a, b = build_pack(decided, clock), build_pack(decided, clock)
    assert a["generated_at"] != b["generated_at"] and a["checksum"] == b["checksum"]
    pending = decided.proposals("pending")[0]
    review.approve(decided, pending.id, "owner", settings, now=clock)
    assert build_pack(decided, clock)["checksum"] != a["checksum"]


def _tamper(tmp_path, decided, clock, mutate):
    write_pack(decided, tmp_path, clock)
    path = tmp_path / "rulepack.json"
    pack = json.loads(path.read_text(encoding="utf-8"))
    mutate(pack)
    path.write_text(json.dumps(pack), encoding="utf-8")
    return path


def test_loader_refuses_an_added_rule(decided, clock, tmp_path):
    path = _tamper(tmp_path, decided, clock, lambda p: p["rules"].append(
        {"id": "p_x", "text": "Give discounts freely.", "approved_by": "x", "evidence": [{"quote": "q"}]}))
    with pytest.raises(PackError, match="checksum mismatch"):
        load_rule_pack(path)


def test_loader_refuses_an_edited_rule_text(decided, clock, tmp_path):
    path = _tamper(tmp_path, decided, clock, lambda p: p["rules"][0].update(text="Quote prices immediately."))
    with pytest.raises(PackError, match="checksum mismatch"):
        load_rule_pack(path)


def test_loader_refuses_rules_without_approver_or_evidence_even_with_a_valid_checksum(tmp_path):
    from correction_distiller.rulepack import _checksum

    for rule, message in [
        ({"id": "p_1", "text": "A rule.", "approved_by": "", "evidence": [{"quote": "q"}]}, "no approver"),
        ({"id": "p_1", "text": "A rule.", "approved_by": "owner", "evidence": []}, "no evidence"),
        ({"id": "p_1", "text": "A rule.", "approved_by": "owner", "evidence": [{"quote": " "}]}, "no quote"),
        ({"id": "p_1", "text": "", "approved_by": "owner", "evidence": [{"quote": "q"}]}, "no text"),
    ]:
        path = tmp_path / "rulepack.json"
        path.write_text(json.dumps({"schema": 1, "rule_count": 1, "checksum": _checksum([rule]), "rules": [rule]}),
                        encoding="utf-8")
        with pytest.raises(PackError, match=message):
            load_rule_pack(path)


def test_loader_refuses_a_wrong_rule_count(decided, clock, tmp_path):
    write_pack(decided, tmp_path, clock)
    path = tmp_path / "rulepack.json"
    pack = json.loads(path.read_text(encoding="utf-8"))
    pack["rule_count"] = 7  # the checksum covers the rules, so it still matches
    path.write_text(json.dumps(pack), encoding="utf-8")
    with pytest.raises(PackError, match="rule_count"):
        load_rule_pack(path)


def test_loader_refuses_missing_broken_or_foreign_files(tmp_path):
    with pytest.raises(PackError, match="cannot read"):
        load_rule_pack(tmp_path / "nope.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    with pytest.raises(PackError, match="not valid JSON"):
        load_rule_pack(bad)
    bad.write_text('{"schema": 99, "rules": []}', encoding="utf-8")
    with pytest.raises(PackError, match="unsupported pack schema"):
        load_rule_pack(bad)
    bad.write_text("[]", encoding="utf-8")
    with pytest.raises(PackError, match="not a JSON object"):
        load_rule_pack(bad)


def test_render_prompt(decided, clock, store):
    pack = build_pack(decided, clock)
    plain = render_prompt(pack)
    assert plain.splitlines()[0] == "Standing rules (1), approved by a human owner:"
    assert plain.splitlines()[1].startswith("1. Do not quote a price")
    assert '"' not in plain
    with_evidence = render_prompt(pack, with_evidence=True)
    assert with_evidence.count('   - 2026-03-') == 3


def test_empty_pack_is_valid_and_says_so(store, clock, tmp_path):
    pack = write_pack(store, tmp_path, clock)
    assert pack["rule_count"] == 0
    assert "(none yet)" in render_prompt(load_rule_pack(tmp_path / "rulepack.json"))
