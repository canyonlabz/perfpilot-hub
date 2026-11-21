"""Vendor adapter contract for building AG2 typed model configs.

``services/llm_provider.py`` is the vendor-agnostic coordinator: it owns config
merging, TLS ``verify`` resolution, the shared ``httpx2`` client, and lifecycle,
then dispatches to the adapter registered for the active provider. Each adapter
in this package owns exactly one vendor's specifics:

- which keys it reads out of the merged config dict,
- how it shapes that vendor's AG2 typed config (field layout differs per vendor),
- its own sampling / temperature policy.

This keeps vendor construction *behavior* out of the coordinator while the
coordinator keeps default *values* and cross-cutting concerns (TLS, lifecycle).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ag2.config import ModelConfig


@runtime_checkable
class ProviderAdapter(Protocol):
    """Maps a normalized provider-config dict onto a vendor's AG2 typed config.

    Implementations declare the provider id(s) they serve via ``provider_ids``
    and build the vendor's ``ModelConfig`` in :meth:`build_ag2_config`.
    """

    #: Provider identifier(s) this adapter is registered under (e.g. the
    #: OpenAI-standard family serves ``("openai", "azure_openai", "ollama")``).
    provider_ids: tuple[str, ...]

    def build_ag2_config(self, config: dict, *, http_client: Any) -> "ModelConfig":
        """Build the vendor's AG2 typed config from a merged config dict.

        Args:
            config: The merged provider-config dict (YAML block + env credentials
                + defaults), as held by the coordinating ``LLMProvider``.
            http_client: The coordinator's shared ``httpx2.AsyncClient``, already
                carrying the resolved TLS ``verify`` value. Adapters attach it so
                corporate CA settings reach the vendor's LLM calls.

        Returns:
            A concrete ``ag2.config.ModelConfig`` subtype for the vendor.
        """
        ...
