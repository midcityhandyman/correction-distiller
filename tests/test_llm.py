import json

import pytest

from correction_distiller.config import ConfigError, Settings, load_settings
from correction_distiller.evidence import verify_draft
from correction_distiller.llm import (
    HttpChatLLM,
    LLMError,
    MockLLM,
    build_user_prompt,
    make_llm,
    parse_draft_response,
)


def test_mock_draft_is_deterministic_and_passes_the_gate(corrections):
    members = corrections[:3]
    first = MockLLM().draft_rule(members)
    second = MockLLM().draft_rule(list(reversed(members)))
    assert first == second
    assert first.text == "Do not quote a price before the site visit is booked."
    assert first.scope == "replying to a new estimate request"
    assert "3 times" in first.rationale
    assert verify_draft(first, members).ok


def test_mock_declines_an_empty_cluster():
    assert MockLLM().draft_rule([]) is None


def test_prompt_lists_every_correction_with_id_date_and_quote(corrections):
    prompt = build_user_prompt(corrections[:2])
    for c in corrections[:2]:
        assert c.id in prompt and c.said_on in prompt and json.dumps(c.quote) in prompt


def test_parse_reply_with_surrounding_text():
    reply = 'Here you go:\n{"rule": "Book first.", "rationale": "r", "scope": "s", ' \
            '"citations": [{"correction_id": "c_1", "quote": "Book the visit first."}]}\nDone.'
    draft = parse_draft_response(reply)
    assert draft.text == "Book first."
    assert draft.citations[0].correction_id == "c_1"


def test_parse_no_rule_reply_means_declined():
    assert parse_draft_response('{"no_rule": true, "reason": "unrelated"}') is None


@pytest.mark.parametrize(
    "reply",
    [
        "I could not decide.",
        "{not json}",
        "[1, 2]",
        '{"rule": ""}',
        '{"rule": "x", "citations": "c_1"}',
        '{"rule": "x", "citations": ["c_1"]}',
        '{"rule": "x", "citations": [{"correction_id": "c_1"}]}',
    ],
)
def test_malformed_replies_raise(reply):
    with pytest.raises(LLMError):
        parse_draft_response(reply)


def test_http_client_builds_request_and_parses_reply(corrections):
    members = corrections[:2]
    seen = {}

    def transport(url, headers, body, timeout):
        seen.update(url=url, headers=headers, body=body, timeout=timeout)
        content = json.dumps(
            {"rule": "Book the visit before quoting.", "scope": "estimates",
             "citations": [{"correction_id": m.id, "quote": m.quote} for m in members]}
        )
        return {"choices": [{"message": {"content": content}}]}

    llm = HttpChatLLM("https://llm.example.com/v1/", "test-key", "test-model", 5.0, transport)
    draft = llm.draft_rule(members)
    assert seen["url"] == "https://llm.example.com/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer test-key"
    assert seen["body"]["model"] == "test-model"
    assert seen["body"]["messages"][1]["content"] == build_user_prompt(members)
    assert llm.name == "http:test-model"
    assert verify_draft(draft, members).ok


def test_http_client_rejects_unexpected_response_shape(corrections):
    llm = HttpChatLLM("https://llm.example.com/v1", "k", "m", transport=lambda *a: {"oops": 1})
    with pytest.raises(LLMError, match="unexpected response shape"):
        llm.draft_rule(corrections[:2])


def test_http_client_needs_full_configuration():
    with pytest.raises(LLMError, match="CD_LLM_BASE_URL"):
        HttpChatLLM("", "", "")


def test_default_provider_is_the_offline_mock():
    assert isinstance(make_llm(load_settings(env={})), MockLLM)


def test_settings_from_env():
    env = {"CD_OWNERS": "owner, ops-lead", "CD_MIN_CITATIONS": "3", "CD_LLM_PROVIDER": "HTTP",
           "CD_LLM_BASE_URL": "https://llm.example.com/v1", "CD_LLM_API_KEY": "k", "CD_LLM_MODEL": "m"}
    s = load_settings(env=env, db_path="x.db")
    assert s.owners == ("owner", "ops-lead")
    assert s.min_citations == 3
    assert s.db_path == "x.db"
    assert isinstance(make_llm(s), HttpChatLLM)


@pytest.mark.parametrize(
    "env",
    [{"CD_MIN_CITATIONS": "0"}, {"CD_MIN_OCCURRENCES": "1"}, {"CD_SIMILARITY_THRESHOLD": "abc"},
     {"CD_SIMILARITY_THRESHOLD": "0"}, {"CD_LLM_PROVIDER": "telepathy"}],
)
def test_bad_settings_are_refused(env):
    with pytest.raises(ConfigError):
        load_settings(env=env)


def test_evidence_cannot_be_configured_away():
    with pytest.raises(ConfigError):
        Settings(min_citations=0)
