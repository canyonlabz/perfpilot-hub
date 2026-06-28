"""LLM provider abstraction for chat completions.

Mirrors the existing `EmbeddingProvider` pattern from
`perfmemory-mcp/services/embeddings.py` but produces chat-completion responses
instead of embedding vectors. Supports OpenAI, Azure OpenAI, and Ollama
through a single async interface.

Two consumers:

1. **AG2 agents** read `to_ag2_config()` to get an AG2-compatible
   `llm_config` dict. AG2 then calls the OpenAI client itself; our wrapper
   stays out of that hot path.
2. **Direct callers** (smoke tests, the AG-UI bridge, ad-hoc tooling) call
   `chat()` for a normalized response dict that is identical in shape across
   all three providers.

Heavy imports (`openai`, `httpx`) are deferred into the lazy client getters so
this module can be imported in environments without those packages installed
(structural smoke tests, IDE indexing, etc.).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

VALID_PROVIDERS = ("openai", "azure_openai", "ollama")
DEFAULT_OPENAI_CHAT_MODEL = "gpt-4o-mini"
DEFAULT_AZURE_DEPLOYMENT = "gpt-4o"
DEFAULT_OLLAMA_MODEL = "llama3.1"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_AZURE_API_VERSION = "2024-02-15-preview"
DEFAULT_TEMPERATURE = 0.2

# SSL verification modes - mirrors the pattern used by blazemeter-mcp,
# datadog-mcp, and confluence-mcp so operators have one mental model
# across the suite.
VALID_SSL_MODES = ("ca_bundle", "disabled", "system")
DEFAULT_SSL_MODE = "ca_bundle"


# =============================================================================
# Configuration loading
# =============================================================================

def load_agents_yaml(framework_dir: Optional[Path] = None) -> dict:
    """Load `config/agents.yaml`, falling back to `agents.example.yaml`.

    Mirrors PerfMemory's `load_taxonomy` candidate-resolution pattern.

    Args:
        framework_dir: Path to `agent-framework/`. Defaults to the parent of
            this module's folder.

    Returns:
        The parsed YAML dict, or `{}` if neither file exists (with a warning).
    """
    import yaml

    if framework_dir is None:
        framework_dir = Path(__file__).resolve().parent.parent

    candidates = (
        framework_dir / "config" / "agents.yaml",
        framework_dir / "config" / "agents.example.yaml",
    )
    for candidate in candidates:
        if candidate.exists():
            log.debug("Loading agents.yaml from %s", candidate)
            with open(candidate, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}

    log.warning("Neither agents.yaml nor agents.example.yaml found under %s/config/", framework_dir)
    return {}


def merge_env_credentials(provider_config: dict, env: Optional[dict] = None) -> dict:
    """Enrich a provider-config block with credentials from environment variables.

    The YAML block stored in `agents.yaml` (or per-agent `config.yaml`)
    declares behavior - which provider, which model, what temperature -
    while credentials live in `.env`. This helper combines them so the
    `LLMProvider` constructor sees a single dict with everything it needs.

    Recognized env vars:
        LLM_PROVIDER  (fallback for the `provider` field if YAML is silent)
        OPENAI_API_KEY, OPENAI_MODEL
        AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT,
        AZURE_OPENAI_API_VERSION, AZURE_OPENAI_DEPLOYMENT
        OLLAMA_BASE_URL, OLLAMA_MODEL
        REQUESTS_CA_BUNDLE / SSL_CERT_FILE  (corporate CA bundle path; same
            convention as blazemeter-mcp, datadog-mcp, confluence-mcp)

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
    enriched.setdefault("openai_model", env.get("OPENAI_MODEL", DEFAULT_OPENAI_CHAT_MODEL))

    enriched.setdefault("azure_api_key", env.get("AZURE_OPENAI_API_KEY", ""))
    enriched.setdefault("azure_endpoint", env.get("AZURE_OPENAI_ENDPOINT", ""))
    enriched.setdefault("azure_api_version", env.get("AZURE_OPENAI_API_VERSION", DEFAULT_AZURE_API_VERSION))
    enriched.setdefault("azure_deployment", env.get("AZURE_OPENAI_DEPLOYMENT", DEFAULT_AZURE_DEPLOYMENT))

    enriched.setdefault("ollama_base_url", env.get("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL))
    enriched.setdefault("ollama_model", env.get("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL))

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

def build_llm_config(agent_llm_block: dict | None = None) -> dict:
    """Build an AG2-compatible ``llm_config`` dict for a specialist agent.

    This is the convenience function every specialist ``build_*_agent()``
    factory calls.  It merges the per-agent YAML ``llm_provider`` block
    (if any) with environment-variable credentials and the global
    ``config/agents.yaml`` defaults, then returns the dict that
    ``autogen.ConversableAgent(llm_config=...)`` expects.

    Args:
        agent_llm_block: The ``llm_provider`` sub-dict from the agent's
            own ``config.yaml``.  ``None`` means "use global defaults".
    """
    if agent_llm_block:
        merged = merge_env_credentials(agent_llm_block)
    else:
        merged = load_default_provider_config()
    return LLMProvider(merged).to_ag2_config()


class LLMProvider:
    """Abstraction layer for chat-completion LLM providers.

    Supports OpenAI, Azure OpenAI, and Ollama. The provider is selected via
    the `provider` field of the config dict at initialization. All providers
    expose the same async interface: `chat(messages, **kwargs) -> dict`.

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
        self._openai_client = None
        self._azure_client = None
        self._ollama_client = None

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
            temperature = self.config.get("temperature", DEFAULT_TEMPERATURE)

        if self.provider == "openai":
            return await self._chat_openai(messages, temperature, max_tokens, extra)
        if self.provider == "azure_openai":
            return await self._chat_azure_openai(messages, temperature, max_tokens, extra)
        if self.provider == "ollama":
            return await self._chat_ollama(messages, temperature, max_tokens, extra)
        raise ValueError(f"Unknown LLM provider: {self.provider}")

    def to_ag2_config(self) -> dict:
        """Return an AG2-compatible `llm_config` dict.

        AG2 (`autogen.ConversableAgent`) takes an `llm_config` dict at
        construction time and calls the LLM itself. This helper produces the
        exact shape AG2 expects so each agent can do
        `ConversableAgent(llm_config=provider.to_ag2_config(), ...)`.

        Notes:
            - For Ollama we use `api_type: "openai"` plus a `base_url`
              pointing at Ollama's OpenAI-compatible endpoint
              (`http://host:port/v1`). This avoids requiring AG2's optional
              `ollama` extra and works with any AG2 version that ships an
              OpenAI client.
        """
        temperature = self.config.get("temperature", DEFAULT_TEMPERATURE)

        if self.provider == "openai":
            entry = {
                "model": self.config.get("openai_model", DEFAULT_OPENAI_CHAT_MODEL),
                "api_key": self.config.get("openai_api_key", ""),
                "api_type": "openai",
            }
        elif self.provider == "azure_openai":
            entry = {
                "model": self.config.get("azure_deployment", DEFAULT_AZURE_DEPLOYMENT),
                "api_key": self.config.get("azure_api_key", ""),
                "api_type": "azure",
                "azure_endpoint": self.config.get("azure_endpoint", ""),
                "api_version": self.config.get("azure_api_version", DEFAULT_AZURE_API_VERSION),
            }
        elif self.provider == "ollama":
            base_url = self.config.get("ollama_base_url", DEFAULT_OLLAMA_BASE_URL).rstrip("/")
            entry = {
                "model": self.config.get("ollama_model", DEFAULT_OLLAMA_MODEL),
                "api_key": "ollama",  # Ollama ignores the key; OpenAI client requires non-empty.
                "api_type": "openai",
                "base_url": f"{base_url}/v1",
            }
        else:
            raise ValueError(f"Unknown LLM provider: {self.provider}")

        return {"config_list": [entry], "temperature": temperature}

    def get_model_name(self) -> str:
        """Return the model identifier for the current provider."""
        if self.provider == "openai":
            return self.config.get("openai_model", DEFAULT_OPENAI_CHAT_MODEL)
        if self.provider == "azure_openai":
            return self.config.get("azure_deployment", DEFAULT_AZURE_DEPLOYMENT)
        if self.provider == "ollama":
            return self.config.get("ollama_model", DEFAULT_OLLAMA_MODEL)
        return "unknown"

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
            import httpx
            from openai import AsyncOpenAI
            verify = self._resolve_verify()
            http_client = httpx.AsyncClient(verify=verify)
            self._openai_client = AsyncOpenAI(
                api_key=self.config.get("openai_api_key"),
                http_client=http_client,
            )
        return self._openai_client

    def _get_azure_client(self):
        if self._azure_client is None:
            import httpx
            from openai import AsyncAzureOpenAI
            verify = self._resolve_verify()
            http_client = httpx.AsyncClient(verify=verify)
            self._azure_client = AsyncAzureOpenAI(
                api_key=self.config.get("azure_api_key"),
                azure_endpoint=self.config.get("azure_endpoint"),
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
        model = self.config.get("openai_model", DEFAULT_OPENAI_CHAT_MODEL)
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
        deployment = self.config.get("azure_deployment", DEFAULT_AZURE_DEPLOYMENT)
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
        model = self.config.get("ollama_model", DEFAULT_OLLAMA_MODEL)

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
