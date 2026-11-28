"""Vendor-agnostic LLM provider abstraction for chat completions.

Mirrors the existing `EmbeddingProvider` pattern from
`perfmemory-mcp/services/embeddings.py` but produces chat-completion responses
instead of embedding vectors. OpenAI is the default; Azure OpenAI, Ollama, and
Anthropic are also supported, and new vendors can be added without touching this
module.

This module is the vendor-agnostic coordinator. It owns configuration merging,
TLS `verify` resolution, the shared `httpx2` client, and lifecycle, then
dispatches to the per-vendor adapter registered in `services.model_adapters`.
Each vendor's typed-config construction (field layout, sampling policy, required
extras) lives in its own adapter, because vendor signatures genuinely differ
(for example, Anthropic nests the model in a different shape and rejects a
`temperature`).

Two consumers:

1. **AG2 agents** read `to_ag2_config()` to get an AG2 typed `ModelConfig`
   (e.g. `ag2.config.OpenAICompletionsConfig` or `AnthropicConfig`). AG2 then
   calls the LLM itself; our wrapper stays out of that hot path.
2. **Direct callers** (smoke tests, the AG-UI bridge, ad-hoc tooling) call
   `chat()` for a normalized response dict. The direct `chat()` path currently
   supports the OpenAI-standard providers (OpenAI / Azure OpenAI / Ollama).

Heavy imports (`openai`, `httpx`, `httpx2`, `ag2.config`, vendor libraries) are
deferred into the lazy client getters and the adapter builders so this module
can be imported in environments without those packages installed (structural
smoke tests, IDE indexing, etc.).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from ag2.config import ModelConfig

log = logging.getLogger(__name__)

VALID_PROVIDERS = ("openai", "azure_openai", "ollama", "anthropic")
DEFAULT_OPENAI_CHAT_MODEL = "gpt-5-mini"
DEFAULT_AZURE_DEPLOYMENT = "gpt-5-mini"
DEFAULT_OLLAMA_MODEL = "llama3.1"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_AZURE_API_VERSION = "2024-02-15-preview"
# Anthropic default model is a placeholder alias; operators set the real model
# via the `ANTHROPIC_MODEL` env var or the agent's `llm_provider` YAML block.
DEFAULT_ANTHROPIC_MODEL = "claude-3-5-sonnet-latest"
DEFAULT_ANTHROPIC_MAX_TOKENS = 4096
DEFAULT_TEMPERATURE = 1.0

# Single source of the per-provider default model. Adding a vendor is one entry
# here (plus its adapter) rather than a new constant + setdefault + adapter key.
DEFAULT_MODELS = {
    "openai": DEFAULT_OPENAI_CHAT_MODEL,
    "azure_openai": DEFAULT_AZURE_DEPLOYMENT,
    "ollama": DEFAULT_OLLAMA_MODEL,
    "anthropic": DEFAULT_ANTHROPIC_MODEL,
}

# Provider-scoped env var that supplies the model when the YAML block omits
# `llm_model`. (For Azure the "model" is the deployment name.)
PROVIDER_MODEL_ENV = {
    "openai": "OPENAI_MODEL",
    "azure_openai": "AZURE_OPENAI_DEPLOYMENT",
    "ollama": "OLLAMA_MODEL",
    "anthropic": "ANTHROPIC_MODEL",
}

# Legacy provider-specific model keys. These are no longer accepted in a
# provider block: the single generic `llm_model` replaces all of them. They are
# listed so the resolver can raise a clear, actionable error if an old config is
# loaded (this also catches a key that does not match the active provider).
LEGACY_MODEL_KEYS = ("openai_model", "azure_deployment", "ollama_model", "anthropic_model")


def _resolve_llm_model(
    config: Mapping[str, Any],
    provider: str,
    env: Optional[Mapping[str, str]] = None,
) -> str:
    """Resolve the single generic model identifier (``llm_model``).

    Precedence:
        1. ``config["llm_model"]`` (the YAML block's generic key)
        2. the provider-scoped env var in :data:`PROVIDER_MODEL_ENV`
           (only consulted when ``env`` is provided)
        3. the provider default in :data:`DEFAULT_MODELS`

    The legacy provider-specific model keys (``openai_model`` /
    ``azure_deployment`` / ``ollama_model`` / ``anthropic_model``) are no longer
    supported. If any of them appears in ``config`` this raises ``ValueError``
    naming ``llm_model`` - which also covers a legacy key that does not match the
    active provider (the old "silent wrong model" hazard).

    For ``azure_openai`` the resolved value is the deployment name.
    """
    present_legacy = [k for k in LEGACY_MODEL_KEYS if k in config]
    if present_legacy:
        raise ValueError(
            "Legacy model key(s) {} are no longer supported. Use a single "
            "'llm_model' in the llm_provider block instead (for azure_openai "
            "this is the deployment name).".format(
                ", ".join(repr(k) for k in present_legacy)
            )
        )

    model = config.get("llm_model")
    if model not in (None, ""):
        return str(model)

    if env:
        env_key = PROVIDER_MODEL_ENV.get(provider)
        if env_key:
            env_val = env.get(env_key, "").strip()
            if env_val:
                return env_val

    return DEFAULT_MODELS.get(provider, DEFAULT_OPENAI_CHAT_MODEL)

# SSL verification modes - mirrors the pattern used by blazemeter-mcp,
# datadog-mcp, and confluence-mcp so operators have one mental model
# across the suite.
VALID_SSL_MODES = ("ca_bundle", "disabled", "system")
DEFAULT_SSL_MODE = "ca_bundle"


# =============================================================================
# Configuration loading
# =============================================================================

def load_agents_yaml(framework_dir: Optional[Path] = None) -> dict:
    """Load ``config/agents.yaml``, falling back to ``agents.example.yaml``.

    Delegates to ``config_loader.load_global_config()`` which handles
    candidate resolution, utf-8-sig encoding, caching, and error handling
    centrally.

    Args:
        framework_dir: Path to ``agent-framework/backend/``. Defaults to the
            parent of this module's folder.

    Returns:
        The parsed YAML dict, or ``{}`` if neither file exists (with a warning).
    """
    from utils import config_loader

    return config_loader.load_global_config(framework_dir=framework_dir)


def merge_env_credentials(provider_config: dict, env: Optional[Mapping[str, str]] = None) -> dict:
    """Enrich a provider-config block with credentials from environment variables.

    The YAML block stored in `agents.yaml` (or per-agent `config.yaml`)
    declares behavior - which provider, which model, what temperature -
    while credentials live in `.env`. This helper combines them so the
    `LLMProvider` constructor sees a single dict with everything it needs.

    Recognized env vars:
        LLM_PROVIDER  (fallback for the `provider` field if YAML is silent)
        OPENAI_API_KEY
        AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_VERSION
        OLLAMA_BASE_URL
        ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL, ANTHROPIC_MAX_TOKENS
        OPENAI_MODEL / AZURE_OPENAI_DEPLOYMENT / OLLAMA_MODEL / ANTHROPIC_MODEL
            (provider-scoped model fallbacks, used ONLY when the YAML block
            omits `llm_model`; see `_resolve_llm_model`)
        REQUESTS_CA_BUNDLE / SSL_CERT_FILE  (corporate CA bundle path; same
            convention as blazemeter-mcp, datadog-mcp, confluence-mcp)

    The model is normalized into a single generic `llm_model` key by
    `_resolve_llm_model`. The legacy provider-specific model keys
    (`openai_model` / `azure_deployment` / `ollama_model` / `anthropic_model`)
    are no longer accepted and raise `ValueError`.

    YAML values win over env values when both are present (so per-agent
    overrides are honored). The precedence chain for `provider` itself is:
        YAML block > LLM_PROVIDER env var > LLMProvider's hardcoded default.
    """
    env = env if env is not None else os.environ
    enriched = dict(provider_config) if provider_config else {}

    env_provider = env.get("LLM_PROVIDER", "").strip()
    if env_provider:
        enriched.setdefault("provider", env_provider)

    enriched.setdefault("openai_api_key", env.get("OPENAI_API_KEY", ""))

    enriched.setdefault("azure_api_key", env.get("AZURE_OPENAI_API_KEY", ""))
    enriched.setdefault("azure_endpoint", env.get("AZURE_OPENAI_ENDPOINT", ""))
    enriched.setdefault("azure_api_version", env.get("AZURE_OPENAI_API_VERSION", DEFAULT_AZURE_API_VERSION))

    enriched.setdefault("ollama_base_url", env.get("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL))

    enriched.setdefault("anthropic_api_key", env.get("ANTHROPIC_API_KEY", ""))
    enriched.setdefault("anthropic_base_url", env.get("ANTHROPIC_BASE_URL", ""))
    enriched.setdefault(
        "anthropic_max_tokens",
        env.get("ANTHROPIC_MAX_TOKENS", DEFAULT_ANTHROPIC_MAX_TOKENS),
    )

    # Normalize the model into a single generic `llm_model` (raises on any
    # legacy provider-specific model key). Resolved after `provider` so the
    # right env fallback / default is chosen.
    provider = enriched.get("provider") or "openai"
    enriched["llm_model"] = _resolve_llm_model(enriched, provider, env=env)

    enriched.setdefault("temperature", DEFAULT_TEMPERATURE)

    # TLS verification - same convention as the other MCP servers.
    # `ssl_verification` may already be set in the YAML block; do not overwrite.
    enriched.setdefault("ssl_verification", DEFAULT_SSL_MODE)
    enriched.setdefault(
        "ca_bundle",
        env.get("REQUESTS_CA_BUNDLE") or env.get("SSL_CERT_FILE") or "",
    )
    return enriched


def load_default_provider_config(framework_dir: Optional[Path] = None) -> dict:
    """Build the global-fallback provider config dict.

    Reads `config/agents.yaml` -> `default_llm_provider` block, merges in env
    credentials, and returns the resulting dict suitable for `LLMProvider(...)`.

    The returned dict always has every key any provider could need, even if
    the configured `provider` does not consume them. This keeps the
    `LLMProvider` constructor agnostic to which provider is active.
    """
    agents_yaml = load_agents_yaml(framework_dir)
    yaml_block = agents_yaml.get("default_llm_provider", {}) or {}
    return merge_env_credentials(yaml_block)


# =============================================================================
# LLMProvider
# =============================================================================

def build_llm_config(agent_llm_block: dict | None = None) -> "ModelConfig":
    """Build an AG2 typed ``ModelConfig`` for a specialist agent.

    This is the convenience function every specialist ``build_*_agent()``
    factory calls.  It merges the per-agent YAML ``llm_provider`` block
    (if any) with environment-variable credentials and the global
    ``config/agents.yaml`` defaults, then returns the typed config that
    ``ag2.Agent(config=...)`` expects.

    Args:
        agent_llm_block: The ``llm_provider`` sub-dict from the agent's
            own ``config.yaml``.  ``None`` means "use global defaults".

    Note:
        The returned config owns an ``httpx2.AsyncClient``. The caller should
        retain the originating ``LLMProvider`` (or the agent holding it) and call
        ``close()`` at shutdown so the client's connection pool is released.
        When you need that lifecycle handle, prefer ``build_llm_provider()``.
    """
    return build_llm_provider(agent_llm_block).to_ag2_config()


def build_llm_provider(agent_llm_block: dict | None = None) -> "LLMProvider":
    """Build a retained ``LLMProvider`` for a specialist agent.

    Same merge logic as :func:`build_llm_config` (per-agent YAML ``llm_provider``
    block, else global ``config/agents.yaml`` defaults, plus env credentials),
    but returns the ``LLMProvider`` instance so the caller owns its lifecycle.
    The provider holds the ``httpx2`` client used by the agent's typed config;
    call ``close()`` at shutdown to release the connection pool.

    Args:
        agent_llm_block: The ``llm_provider`` sub-dict from the agent's own
            ``config.yaml``. ``None`` means "use global defaults".
    """
    if agent_llm_block:
        merged = merge_env_credentials(agent_llm_block)
    else:
        merged = load_default_provider_config()
    return LLMProvider(merged)


class LLMProvider:
    """Abstraction layer for chat-completion LLM providers.

    Supports OpenAI, Azure OpenAI, Ollama, and Anthropic. The provider is
    selected via the `provider` field of the config dict at initialization.
    All providers expose the same async interface: `chat(messages, **kwargs)
    -> dict` (the direct `chat()` path covers the OpenAI-standard providers).

    HTTP / SDK clients are created lazily on first use and reused across
    calls to avoid spinning up a new connection pool / SSL context every time.

    Args:
        config: Provider config dict, typically produced by
            `load_default_provider_config()` or `merge_env_credentials()`.
    """

    def __init__(self, config: dict):
        self.provider = config.get("provider", "openai")
        if self.provider not in VALID_PROVIDERS:
            log.warning(
                "LLMProvider initialized with unknown provider '%s' (expected one of %s)",
                self.provider,
                VALID_PROVIDERS,
            )
        self.config = dict(config)
        # Guarantee the resolved provider is visible in the config dict so the
        # per-vendor adapter (which reads config["provider"]) sees the same
        # value even when the caller omitted it and relied on the default.
        self.config.setdefault("provider", self.provider)
        # Normalize the model to a single generic `llm_model`. Runs on every
        # construction path (including direct `LLMProvider({...})` that bypasses
        # `merge_env_credentials`). Env is not consulted here; the env fallback
        # belongs to `merge_env_credentials`. Raises on any legacy model key.
        self.config["llm_model"] = _resolve_llm_model(self.config, self.provider, env=None)
        self._openai_client = None
        self._azure_client = None
        self._ollama_client = None
        self._agent_http_client = None

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    async def chat(
        self,
        messages: list[dict],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **extra: Any,
    ) -> dict:
        """Run a chat completion against the configured provider.

        Args:
            messages: List of `{"role": "...", "content": "..."}` dicts in
                the standard OpenAI chat shape.
            temperature: Sampling temperature; falls back to config value or
                `DEFAULT_TEMPERATURE`.
            max_tokens: Optional cap on response length.
            **extra: Provider-specific extras passed through verbatim.

        Returns:
            Normalized response dict identical in shape across providers:
                {
                    "content": str,           # assistant message text
                    "model":   str,           # model that responded
                    "usage":   dict | None,   # token counts when available
                    "raw":     Any,           # provider-native response object
                }

        Raises:
            ValueError: If the configured provider is unknown.
            RuntimeError: If the underlying API call fails.
        """
        if temperature is None:
            configured = self.config.get("temperature", DEFAULT_TEMPERATURE)
            temperature = DEFAULT_TEMPERATURE if configured is None else float(configured)

        if self.provider == "openai":
            return await self._chat_openai(messages, temperature, max_tokens, extra)
        if self.provider == "azure_openai":
            return await self._chat_azure_openai(messages, temperature, max_tokens, extra)
        if self.provider == "ollama":
            return await self._chat_ollama(messages, temperature, max_tokens, extra)
        raise ValueError(f"Unknown LLM provider: {self.provider}")

    def to_ag2_config(self) -> "ModelConfig":
        """Return an AG2 typed ``ModelConfig`` for the configured provider.

        Vendor-agnostic: this dispatches to the per-vendor adapter registered in
        ``services.model_adapters`` and returns whatever typed config that vendor
        uses (``OpenAICompletionsConfig`` for the OpenAI-standard family,
        ``AnthropicConfig`` for Anthropic, and so on). The coordinator supplies
        the merged config dict and the shared ``httpx2`` client; the adapter owns
        the vendor-specific field layout and sampling policy.

        TLS: the resolved ``verify`` value (corporate CA bundle, system store, or
        disabled) is carried by the shared ``httpx2.AsyncClient`` passed to the
        adapter as ``http_client`` - this is what propagates corporate TLS
        settings into the agent's LLM calls for every vendor that accepts it.
        """
        from services.model_adapters import get_adapter

        adapter = get_adapter(self.provider)
        return adapter.build_ag2_config(
            self.config,
            http_client=self._get_agent_http_client(),
        )

    def _get_agent_http_client(self):
        """Lazily build and cache the ``httpx2`` client used by AG2 typed configs.

        ``openai>=3`` and AG2's typed configs use ``httpx2`` for async transport.
        The client carries the resolved TLS ``verify`` value so corporate CA
        settings reach the agent's LLM calls. Cached so repeated
        ``to_ag2_config()`` calls share one connection pool; released by
        ``close()``.
        """
        if self._agent_http_client is None:
            import httpx2
            self._agent_http_client = httpx2.AsyncClient(verify=self._resolve_verify())
        return self._agent_http_client

    def get_model_name(self) -> str:
        """Return the model identifier for the current provider.

        After construction the generic `llm_model` is always resolved, so this
        reads it directly (for `azure_openai` it is the deployment name).
        """
        return self.config.get("llm_model") or DEFAULT_MODELS.get(self.provider, "unknown")

    async def close(self) -> None:
        """Release HTTP / SDK resources held by cached clients.

        Safe to call multiple times. Called during agent process shutdown.
        """
        if self._openai_client is not None:
            try:
                await self._openai_client.close()
            except Exception:
                log.warning("Error closing OpenAI client", exc_info=True)
            finally:
                self._openai_client = None

        if self._azure_client is not None:
            try:
                await self._azure_client.close()
            except Exception:
                log.warning("Error closing Azure OpenAI client", exc_info=True)
            finally:
                self._azure_client = None

        if self._ollama_client is not None:
            try:
                await self._ollama_client.aclose()
            except Exception:
                log.warning("Error closing Ollama httpx client", exc_info=True)
            finally:
                self._ollama_client = None

        if self._agent_http_client is not None:
            try:
                await self._agent_http_client.aclose()
            except Exception:
                log.warning("Error closing agent httpx2 client", exc_info=True)
            finally:
                self._agent_http_client = None

    # -------------------------------------------------------------------------
    # Lazy clients
    # -------------------------------------------------------------------------

    def _resolve_verify(self) -> Any:
        """Resolve the httpx `verify=` value from `ssl_verification` + `ca_bundle`.

        Returns one of:
            - True  -> use Python/certifi default trust store ("system" mode,
                       or "ca_bundle" mode with no bundle path set).
            - False -> disable verification entirely ("disabled" mode; never
                       recommended outside a trusted dev environment).
            - str   -> path to a PEM file ("ca_bundle" mode with a CA path,
                       e.g. a corporate / Norton / Zscaler MITM root).
        """
        mode = (self.config.get("ssl_verification") or DEFAULT_SSL_MODE).lower()
        if mode == "disabled":
            log.warning("LLMProvider: TLS verification DISABLED (ssl_verification=disabled)")
            return False
        if mode == "system":
            return True
        # "ca_bundle" (default): use the bundle if provided, else fall back to True.
        bundle = (self.config.get("ca_bundle") or "").strip()
        if bundle:
            return bundle
        return True

    def _get_openai_client(self):
        if self._openai_client is None:
            import httpx2
            from openai import AsyncOpenAI
            verify = self._resolve_verify()
            http_client = httpx2.AsyncClient(verify=verify)
            self._openai_client = AsyncOpenAI(
                api_key=self.config.get("openai_api_key"),
                http_client=http_client,
            )
        return self._openai_client

    def _get_azure_client(self):
        if self._azure_client is None:
            import httpx2
            from openai import AsyncAzureOpenAI
            verify = self._resolve_verify()
            http_client = httpx2.AsyncClient(verify=verify)
            self._azure_client = AsyncAzureOpenAI(
                api_key=self.config.get("azure_api_key"),
                azure_endpoint=self.config.get("azure_endpoint") or "",
                api_version=self.config.get("azure_api_version", DEFAULT_AZURE_API_VERSION),
                http_client=http_client,
            )
        return self._azure_client

    def _get_ollama_client(self):
        if self._ollama_client is None:
            import httpx
            verify = self._resolve_verify()
            self._ollama_client = httpx.AsyncClient(timeout=60.0, verify=verify)
        return self._ollama_client

    # -------------------------------------------------------------------------
    # Per-provider chat implementations
    # -------------------------------------------------------------------------

    async def _chat_openai(
        self,
        messages: list[dict],
        temperature: float,
        max_tokens: Optional[int],
        extra: dict,
    ) -> dict:
        client = self._get_openai_client()
        model = self.config.get("llm_model") or DEFAULT_OPENAI_CHAT_MODEL
        kwargs: dict = {"model": model, "messages": messages, **extra}
        # `max_tokens` is deprecated for OpenAI's reasoning-class models
        # (o1 / o3 / gpt-5 series) which require `max_completion_tokens`.
        # `max_completion_tokens` is accepted by all newer OpenAI chat
        # completion models, so we always use it when a cap is requested.
        if max_tokens is not None:
            kwargs["max_completion_tokens"] = max_tokens
        # Some reasoning-class models reject any non-default temperature.
        # Pass temperature only when it differs from 1.0; this preserves the
        # caller's intent for normal models and stays out of the way for
        # reasoning models, which fix temperature at 1.0 server-side.
        if temperature is not None and abs(temperature - 1.0) > 1e-9:
            kwargs["temperature"] = temperature
        try:
            response = await client.chat.completions.create(**kwargs)
            return _normalize_openai_response(response)
        except Exception as exc:
            # Some models also forbid non-default temperature. Retry once with
            # temperature stripped if the API rejects it.
            msg = str(exc).lower()
            if "temperature" in msg and "temperature" in kwargs:
                kwargs.pop("temperature", None)
                try:
                    response = await client.chat.completions.create(**kwargs)
                    return _normalize_openai_response(response)
                except Exception as exc2:
                    raise RuntimeError(f"OpenAI chat failed: {exc2}") from exc2
            raise RuntimeError(f"OpenAI chat failed: {exc}") from exc

    async def _chat_azure_openai(
        self,
        messages: list[dict],
        temperature: float,
        max_tokens: Optional[int],
        extra: dict,
    ) -> dict:
        client = self._get_azure_client()
        deployment = self.config.get("llm_model") or DEFAULT_AZURE_DEPLOYMENT
        kwargs: dict = {"model": deployment, "messages": messages, **extra}
        if max_tokens is not None:
            kwargs["max_completion_tokens"] = max_tokens
        if temperature is not None and abs(temperature - 1.0) > 1e-9:
            kwargs["temperature"] = temperature
        try:
            response = await client.chat.completions.create(**kwargs)
            return _normalize_openai_response(response)
        except Exception as exc:
            msg = str(exc).lower()
            if "temperature" in msg and "temperature" in kwargs:
                kwargs.pop("temperature", None)
                try:
                    response = await client.chat.completions.create(**kwargs)
                    return _normalize_openai_response(response)
                except Exception as exc2:
                    raise RuntimeError(f"Azure OpenAI chat failed: {exc2}") from exc2
            raise RuntimeError(f"Azure OpenAI chat failed: {exc}") from exc

    async def _chat_ollama(
        self,
        messages: list[dict],
        temperature: float,
        max_tokens: Optional[int],
        extra: dict,
    ) -> dict:
        """Hit Ollama's OpenAI-compatible `/v1/chat/completions` endpoint."""
        client = self._get_ollama_client()
        base_url = self.config.get("ollama_base_url", DEFAULT_OLLAMA_BASE_URL).rstrip("/")
        model = self.config.get("llm_model") or DEFAULT_OLLAMA_MODEL

        body = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        body.update(extra)

        try:
            response = await client.post(f"{base_url}/v1/chat/completions", json=body)
            response.raise_for_status()
            payload = response.json()
            choice = (payload.get("choices") or [{}])[0]
            content = (choice.get("message") or {}).get("content", "") or ""
            return {
                "content": content,
                "model": payload.get("model") or model,
                "usage": payload.get("usage"),
                "raw": payload,
            }
        except Exception as exc:
            raise RuntimeError(f"Ollama chat failed: {exc}") from exc


# =============================================================================
# Helpers
# =============================================================================

def _normalize_openai_response(response: Any) -> dict:
    """Convert an OpenAI / AzureOpenAI ChatCompletion to the normalized dict."""
    choice = response.choices[0] if response.choices else None
    content = ""
    if choice is not None and choice.message is not None:
        content = choice.message.content or ""
    usage = getattr(response, "usage", None)
    usage_dict = None
    if usage is not None:
        try:
            usage_dict = usage.model_dump()
        except AttributeError:
            usage_dict = {
                "prompt_tokens": getattr(usage, "prompt_tokens", None),
                "completion_tokens": getattr(usage, "completion_tokens", None),
                "total_tokens": getattr(usage, "total_tokens", None),
            }
    return {
        "content": content,
        "model": getattr(response, "model", "unknown"),
        "usage": usage_dict,
        "raw": response,
    }
