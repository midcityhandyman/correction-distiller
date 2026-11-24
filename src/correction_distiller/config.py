"""Settings, read from the environment with safe offline defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    db_path: str = ".distiller/distiller.db"
    # Clustering
    similarity_threshold: float = 0.30
    min_occurrences: int = 2  # corrections needed before a cluster counts as recurring
    min_distinct_dates: int = 1  # raise to 2 to ignore a burst within one day
    # Evidence gate
    min_citations: int = 2  # distinct corrections a rule must cite
    min_quote_chars: int = 12  # shortest partial quote that counts as a citation
    max_rule_chars: int = 400
    # Review
    owners: tuple[str, ...] = field(default_factory=tuple)  # empty means any named reviewer
    # LLM
    llm_provider: str = "mock"  # "mock" or "http"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not 0.0 < self.similarity_threshold <= 1.0:
            raise ConfigError("similarity_threshold must be in (0, 1]")
        if self.min_occurrences < 2:
            raise ConfigError("min_occurrences must be at least 2; one correction is not a pattern")
        if self.min_citations < 1:
            raise ConfigError("min_citations must be at least 1; rules without evidence are not allowed")
        if self.llm_provider not in ("mock", "http"):
            raise ConfigError("llm_provider must be 'mock' or 'http'")


def _get(env: Mapping[str, str], key: str, cast, default):
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        return cast(raw)
    except ValueError as exc:
        raise ConfigError(f"{key}={raw!r} is not a valid {cast.__name__}") from exc


def load_settings(env: Mapping[str, str] | None = None, **overrides) -> Settings:
    """Build Settings from CD_* environment variables, then apply overrides."""
    env = os.environ if env is None else env
    defaults = Settings()
    owners = tuple(o.strip() for o in env.get("CD_OWNERS", "").split(",") if o.strip())
    values = dict(
        db_path=_get(env, "CD_DB_PATH", str, defaults.db_path),
        similarity_threshold=_get(env, "CD_SIMILARITY_THRESHOLD", float, defaults.similarity_threshold),
        min_occurrences=_get(env, "CD_MIN_OCCURRENCES", int, defaults.min_occurrences),
        min_distinct_dates=_get(env, "CD_MIN_DISTINCT_DATES", int, defaults.min_distinct_dates),
        min_citations=_get(env, "CD_MIN_CITATIONS", int, defaults.min_citations),
        min_quote_chars=_get(env, "CD_MIN_QUOTE_CHARS", int, defaults.min_quote_chars),
        max_rule_chars=_get(env, "CD_MAX_RULE_CHARS", int, defaults.max_rule_chars),
        owners=owners,
        llm_provider=_get(env, "CD_LLM_PROVIDER", str, defaults.llm_provider).lower(),
        llm_base_url=_get(env, "CD_LLM_BASE_URL", str, defaults.llm_base_url),
        llm_api_key=_get(env, "CD_LLM_API_KEY", str, defaults.llm_api_key),
        llm_model=_get(env, "CD_LLM_MODEL", str, defaults.llm_model),
        llm_timeout_seconds=_get(env, "CD_LLM_TIMEOUT_SECONDS", float, defaults.llm_timeout_seconds),
    )
    values.update({k: v for k, v in overrides.items() if v is not None})
    return Settings(**values)
