"""Anthropic adapter.

Anthropic is the first genuinely different vendor: its AG2 typed config
(``AnthropicConfig``) is shaped unlike OpenAI's. Grounding against the installed
AG2 ``AnthropicConfig`` (a slotted dataclass):

- flat fields ``model``, ``max_tokens`` (default 4096), ``api_key``,
  ``base_url``, ``http_client``;
- ``temperature`` / ``top_p`` / ``top_k`` exist but are **deprecated and
  ignored** - the Anthropic Messages API version AG2 targets rejects them with
  HTTP 400, and AG2 removes them in 1.4.0.

So this adapter maps ``model`` / ``api_key`` / ``base_url`` / ``max_tokens`` and
attaches the shared TLS-aware ``http_client``, and deliberately sends **no
temperature**. Sampling is left at the vendor default.

The ``AnthropicConfig`` import is lazy: when the ``anthropic`` extra is missing or
version-skewed, AG2 exports a ``missing_optional_dependency`` placeholder whose
call raises a clear error - this adapter surfaces that as-is (no install here).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ag2.config import ModelConfig


def _build_kwargs(config: dict, http_client: Any) -> dict[str, Any]:
    """Map the merged config dict onto ``AnthropicConfig`` constructor kwargs.

    Pure (no ag2 import) so the field mapping and the temperature-omission policy
    are unit-testable without the ``anthropic`` vendor library installed.
    """
    from services.llm_provider import (
        DEFAULT_ANTHROPIC_MAX_TOKENS,
        DEFAULT_ANTHROPIC_MODEL,
    )

    model = config.get("anthropic_model") or DEFAULT_ANTHROPIC_MODEL
    api_key = config.get("anthropic_api_key", "")
    base_url = (config.get("anthropic_base_url") or "").strip() or None

    raw_max = config.get("anthropic_max_tokens")
    try:
        max_tokens = int(raw_max) if raw_max not in (None, "") else DEFAULT_ANTHROPIC_MAX_TOKENS
    except (TypeError, ValueError):
        max_tokens = DEFAULT_ANTHROPIC_MAX_TOKENS

    kwargs: dict[str, Any] = {
        "model": model,
        "api_key": api_key,
        "max_tokens": max_tokens,
        "http_client": http_client,
    }
    if base_url:
        kwargs["base_url"] = base_url
    # Intentionally NO temperature / top_p / top_k: Anthropic's Messages API
    # version rejects them (HTTP 400); AG2 marks them deprecated and removes them
    # in 1.4.0. Sampling for Anthropic is left at the vendor default.
    return kwargs


class AnthropicAdapter:
    """Builds ``AnthropicConfig`` from the merged provider-config dict."""

    provider_ids: tuple[str, ...] = ("anthropic",)

    def build_ag2_config(self, config: dict, *, http_client: Any) -> "ModelConfig":
        from ag2.config import AnthropicConfig

        return AnthropicConfig(**_build_kwargs(config, http_client))
