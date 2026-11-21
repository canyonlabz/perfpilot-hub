"""Ollama adapter.

Ollama is not OpenAI-standard on the agent path: AG2 models it with a dedicated
``OllamaConfig`` that takes a ``host`` (the Ollama server base URL, e.g.
``http://localhost:11434``) rather than an OpenAI ``base_url`` with a ``/v1``
suffix. It drives Ollama's native API, so it gets its own adapter instead of
being shoehorned through the OpenAI-compatible endpoint.

Unlike OpenAI reasoning models, Ollama accepts a ``temperature``, so the adapter
forwards it when set.

The ``OllamaConfig`` import is lazy: when the ``ollama`` extra is missing, AG2
exports a ``missing_optional_dependency`` placeholder whose call raises a clear
error - this adapter surfaces that as-is (no install here).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ag2.config import ModelConfig


class OllamaAdapter:
    """Builds ``OllamaConfig`` for a local or remote Ollama server."""

    provider_ids: tuple[str, ...] = ("ollama",)

    def build_ag2_config(self, config: dict, *, http_client: Any) -> "ModelConfig":
        from ag2.config import OllamaConfig

        from services.llm_provider import (
            DEFAULT_OLLAMA_BASE_URL,
            DEFAULT_OLLAMA_MODEL,
            DEFAULT_TEMPERATURE,
        )

        model = config.get("ollama_model", DEFAULT_OLLAMA_MODEL)
        # OllamaConfig uses ``host`` (the server base URL), not an OpenAI
        # ``base_url`` - there is no ``/v1`` suffix on the native path.
        host = (config.get("ollama_base_url") or DEFAULT_OLLAMA_BASE_URL).rstrip("/")

        kwargs: dict[str, Any] = {
            "model": model,
            "host": host,
            "http_client": http_client,
        }
        temperature = config.get("temperature", DEFAULT_TEMPERATURE)
        if temperature is not None:
            kwargs["temperature"] = temperature

        # OllamaConfig inherits ModelConfig's concrete `create_files_client`
        # instead of overriding it, so the type checker wrongly flags it as
        # abstract; it instantiates fine at runtime (verified).
        return OllamaConfig(**kwargs)  # type: ignore[abstract]
