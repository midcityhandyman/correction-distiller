"""The LLM step, behind an interface.

`LLMClient` is the only thing the pipeline knows about. Two implementations:

* `MockLLM`: deterministic, offline, no keys. Used by the tests and the demo.
* `HttpChatLLM`: talks to any chat completions style HTTP endpoint. The
  network call is a replaceable `transport` function so the request building
  and response parsing are tested without a network.

Whatever a client returns is a `DraftRule`, and a DraftRule is untrusted: the
evidence gate checks every citation before anything reaches the queue.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections import Counter
from typing import Callable, Protocol

from .config import Settings
from .models import Citation, Correction, DraftRule
from .text import normalize_ws


class LLMError(RuntimeError):
    """The LLM step failed or returned something unusable."""


class LLMClient(Protocol):
    name: str

    def draft_rule(self, members: list[Correction]) -> DraftRule | None:
        """Draft one rule for a cluster, or return None to decline."""
        ...


# --------------------------------------------------------------------- mock


class MockLLM:
    """Offline stand-in. Picks the shortest quote in the cluster as the rule.

    This is not clever and is not meant to be. It exists so the whole loop
    (cluster, draft, gate, queue, approve, export) runs with no API key, and
    so tests are deterministic.
    """

    name = "mock-llm"

    def draft_rule(self, members: list[Correction]) -> DraftRule | None:
        if not members:
            return None
        ordered = sorted(members, key=lambda c: (c.said_on, c.id))
        canonical = min(ordered, key=lambda c: (len(normalize_ws(c.quote)), c.id))
        text = normalize_ws(canonical.quote)
        text = text[0].upper() + text[1:]
        if text[-1] not in ".!?":
            text += "."
        scope = Counter(c.context for c in ordered).most_common(1)[0][0]
        dates = sorted({c.said_on for c in ordered})
        span = dates[0] if len(dates) == 1 else f"between {dates[0]} and {dates[-1]}"
        rationale = f"The same correction was given {len(ordered)} times ({span})."
        return DraftRule(
            text=text,
            rationale=rationale,
            scope=scope,
            citations=[Citation(c.id, c.quote) for c in ordered],
        )


# --------------------------------------------------------------------- http

SYSTEM_PROMPT = (
    "You turn repeated human corrections of an AI agent into one short standing rule. "
    "Reply with a single JSON object and nothing else. "
    "Keys: rule (one imperative sentence), rationale (one sentence), scope (when the rule applies), "
    "citations (list of objects with correction_id and quote). "
    "Each quote must be copied character for character from the correction it cites. "
    "Do not paraphrase quotes. Cite at least two corrections. "
    'If the corrections do not support one clear rule, reply {"no_rule": true, "reason": "..."}.'
)


def build_user_prompt(members: list[Correction]) -> str:
    lines = ["Corrections (id, date, context, verbatim quote):", ""]
    for c in sorted(members, key=lambda m: (m.said_on, m.id)):
        lines.append(f"- id: {c.id}")
        lines.append(f"  date: {c.said_on}")
        lines.append(f"  context: {c.context}")
        lines.append(f"  quote: {json.dumps(c.quote, ensure_ascii=False)}")
    lines.append("")
    lines.append("Draft the rule as JSON.")
    return "\n".join(lines)


def parse_draft_response(content: str) -> DraftRule | None:
    """Parse the model's reply. None means it declined to draft a rule."""
    start, end = content.find("{"), content.rfind("}")
    if start == -1 or end <= start:
        raise LLMError("reply contains no JSON object")
    try:
        data = json.loads(content[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LLMError(f"reply is not valid JSON: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise LLMError("reply JSON is not an object")
    if data.get("no_rule"):
        return None
    rule = data.get("rule")
    if not isinstance(rule, str) or not rule.strip():
        raise LLMError("reply has no rule text")
    raw_citations = data.get("citations") or []
    if not isinstance(raw_citations, list):
        raise LLMError("citations is not a list")
    citations = []
    for item in raw_citations:
        if not isinstance(item, dict):
            raise LLMError("a citation is not an object")
        cid, quote = item.get("correction_id"), item.get("quote")
        if not isinstance(cid, str) or not isinstance(quote, str):
            raise LLMError("a citation is missing correction_id or quote")
        citations.append(Citation(cid, quote))
    return DraftRule(
        text=rule.strip(),
        rationale=str(data.get("rationale") or "").strip(),
        scope=str(data.get("scope") or "").strip(),
        citations=citations,
    )


Transport = Callable[[str, dict, dict, float], dict]


def urllib_transport(url: str, headers: dict, body: dict, timeout: float) -> dict:
    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise LLMError(f"LLM request failed: {exc}") from exc


class HttpChatLLM:
    """Client for a chat completions style endpoint (POST {base}/chat/completions)."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 30.0,
        transport: Transport = urllib_transport,
    ) -> None:
        if not base_url or not api_key or not model:
            raise LLMError("http provider needs CD_LLM_BASE_URL, CD_LLM_API_KEY and CD_LLM_MODEL")
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.transport = transport
        self.name = f"http:{model}"

    def draft_rule(self, members: list[Correction]) -> DraftRule | None:
        body = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_prompt(members)},
            ],
        }
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}
        reply = self.transport(self.url, headers, body, self.timeout)
        try:
            content = reply["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("unexpected response shape from LLM endpoint") from exc
        if not isinstance(content, str):
            raise LLMError("LLM reply content is not text")
        return parse_draft_response(content)


def make_llm(settings: Settings) -> LLMClient:
    if settings.llm_provider == "http":
        return HttpChatLLM(
            settings.llm_base_url, settings.llm_api_key, settings.llm_model, settings.llm_timeout_seconds
        )
    return MockLLM()
