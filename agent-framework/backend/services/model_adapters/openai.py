"""OpenAI adapter: OpenAI (default) and Azure OpenAI.

Both providers share one AG2 typed config (``OpenAICompletionsConfig``) because
they speak the same OpenAI-standard Chat Completions wire protocol - they differ
only by ``model`` / ``api_key`` / ``base_url``:

- **openai** (default): model + key; the SDK applies its own default endpoint.
- **azure_openai**: the deployment is the model; the configured endpoint is the
  base_url. No azure api-version / api-key-header munging on the agent path.

Other providers get their own adapter because their typed configs diverge:
Anthropic (see ``anthropic.py``) and Ollama (see ``ollama.py``, which uses AG2's
native ``OllamaConfig`` with ``host=`` rather than an OpenAI ``base_url``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ag2.config import ModelConfig

# Reasoning-class model name markers. These models fix sampling temperature
# server-side and reject a non-default ``temperature``, so the agent path must
# not send one (it cannot retry-and-strip the way the direct ``chat()`` path
# does). This sampling policy is OpenAI-specific, hence it lives in this adapter.
_REASONING_MODEL_MARKERS = ("o1", "o3", "o4", "gpt-5", "gpt-6")


def is_reasoning_model(model: str) -> bool:
    """True when ``model`` names a reasoning-class model that rejects temperature."""
    name = (model or "").lower()
    return any(marker in name for marker in _REASONING_MODEL_MARKERS)


class OpenAICompatibleAdapter:
    """Builds ``OpenAICompletionsConfig`` for OpenAI and Azure OpenAI.

    Forward-compat seam: AG2 1.4.0 makes ``OpenAIConfig`` an alias for the
    Responses API, so adopting Responses later is a localized swap here
    (``OpenAICompletionsConfig`` -> ``OpenAIResponsesConfig``, ``max_tokens`` ->
    ``max_output_tokens``) with no change for callers.
    """

    provider_ids: tuple[str, ...] = ("openai", "azure_openai")

    def build_ag2_config(self, config: dict, *, http_client: Any) -> "ModelConfig":
        from ag2.config import OpenAICompletionsConfig

        # Default constants live with the coordinator (the config authority).
        # Imported at call time so importing this adapter never requires the
        # coordinator or ag2 to be importable.
        from services.llm_provider import (
            DEFAULT_AZURE_DEPLOYMENT,
            DEFAULT_OPENAI_CHAT_MODEL,
            DEFAULT_TEMPERATURE,
        )

        provider = config.get("provider", "openai")

        # One generic `llm_model` for both; for Azure it is the deployment name.
        if provider == "azure_openai":
            model = config.get("llm_model") or DEFAULT_AZURE_DEPLOYMENT
            api_key = config.get("azure_api_key", "")
            base_url = (config.get("azure_endpoint") or "").strip() or None
        else:  # "openai" (default)
            model = config.get("llm_model") or DEFAULT_OPENAI_CHAT_MODEL
            api_key = config.get("openai_api_key", "")
            # base_url left unset: the OpenAI SDK applies its own default endpoint.
            base_url = None

        kwargs: dict[str, Any] = {
            "model": model,
            "api_key": api_key,
            "http_client": http_client,
        }
        if base_url:
            kwargs["base_url"] = base_url

        temperature = config.get("temperature", DEFAULT_TEMPERATURE)
        if temperature is not None and not is_reasoning_model(model):
            kwargs["temperature"] = temperature

        return OpenAICompletionsConfig(**kwargs)
