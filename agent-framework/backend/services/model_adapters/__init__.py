"""Per-vendor LLM provider adapters and their registry.

``services/llm_provider.py`` (the vendor-agnostic coordinator) dispatches
``to_ag2_config()`` to the adapter registered here for the active provider. Each
adapter owns one vendor's typed-config construction; see :mod:`.base`.

Importing this package is cheap: adapter modules defer their ``ag2.config`` /
vendor imports to call time, so the registry can be built even in environments
without the AG2 stack (or a given vendor extra) installed.
"""

from __future__ import annotations

from .anthropic import AnthropicAdapter
from .base import ProviderAdapter
from .ollama import OllamaAdapter
from .openai import OpenAICompatibleAdapter

__all__ = [
    "ProviderAdapter",
    "OpenAICompatibleAdapter",
    "AnthropicAdapter",
    "OllamaAdapter",
    "UnknownProviderError",
    "get_adapter",
    "register",
]


class UnknownProviderError(ValueError):
    """Raised when no adapter is registered for a requested provider id."""


_REGISTRY: dict[str, ProviderAdapter] = {}


def register(adapter: ProviderAdapter) -> None:
    """Register ``adapter`` under each of its ``provider_ids``."""
    for provider_id in adapter.provider_ids:
        _REGISTRY[provider_id] = adapter


def get_adapter(provider_id: str) -> ProviderAdapter:
    """Return the adapter registered for ``provider_id``.

    Raises:
        UnknownProviderError: If no adapter is registered for the id.
    """
    try:
        return _REGISTRY[provider_id]
    except KeyError:
        raise UnknownProviderError(
            f"No LLM provider adapter registered for '{provider_id}'. "
            f"Known providers: {sorted(_REGISTRY)}"
        ) from None


# Register the built-in adapters. OpenAI is the default; Azure OpenAI rides the
# OpenAI-standard adapter; Anthropic and Ollama each have their own.
register(OpenAICompatibleAdapter())
register(AnthropicAdapter())
register(OllamaAdapter())
