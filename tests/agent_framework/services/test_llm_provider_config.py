"""Unit tests for the vendor-agnostic AG2 typed-config path.

``services.llm_provider`` is the coordinator; ``services.model_adapters`` holds the
per-vendor adapters. These tests cover:

- the OpenAI-standard adapter (OpenAI / Azure OpenAI / Ollama) building a single
  ``ag2.config.OpenAICompletionsConfig`` that differs only by
  ``model`` / ``api_key`` / ``base_url``,
- an ``httpx2.AsyncClient`` carrying the resolved TLS ``verify`` value attached as
  ``http_client`` (so corporate CA settings reach the agent's LLM calls),
- reasoning-class models (o1/o3/o4/gpt-5/gpt-6) omitting ``temperature``,
- registry dispatch (``get_adapter``), and
- the Anthropic adapter's field mapping and its deliberate omission of
  ``temperature`` (the Anthropic Messages API rejects it).

No network is used: no vendor SDK is called, only the config object is built. The
Anthropic construction test monkeypatches ``ag2.config.AnthropicConfig`` with a
recorder so it runs without a working ``anthropic`` vendor library.
"""

from __future__ import annotations

import pytest

from services.llm_provider import (
    DEFAULT_MODELS,
    LLMProvider,
    build_llm_provider,
    merge_env_credentials,
)
from services.model_adapters.openai import is_reasoning_model

# Skip the whole module if AG2's typed-config package is not importable in this
# environment (e.g. structural smoke runs without the AG2 stack installed).
pytest.importorskip("ag2.config")
from ag2.config import OpenAICompletionsConfig  # noqa: E402  (after importorskip)


# ---------------------------------------------------------------------------
# is_reasoning_model
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model",
    ["o1", "o1-mini", "o3", "o4-mini", "gpt-5", "gpt-5.1", "gpt-6", "GPT-5-TURBO"],
)
def test_is_reasoning_model_true(model):
    assert is_reasoning_model(model) is True


@pytest.mark.parametrize(
    "model",
    ["gpt-4o", "gpt-4.1", "gpt-4o-mini", "llama3", "", "mistral-large"],
)
def test_is_reasoning_model_false(model):
    assert is_reasoning_model(model) is False


# ---------------------------------------------------------------------------
# OpenAI provider
# ---------------------------------------------------------------------------


def test_openai_returns_typed_config_with_http_client():
    provider = LLMProvider(
        {
            "provider": "openai",
            "llm_model": "gpt-4o",
            "openai_api_key": "sk-test-not-a-real-key",
            "temperature": 0.3,
        }
    )
    cfg = provider.to_ag2_config()

    assert isinstance(cfg, OpenAICompletionsConfig)
    assert cfg.model == "gpt-4o"
    assert cfg.api_key == "sk-test-not-a-real-key"
    assert cfg.temperature == 0.3
    # An httpx2 client must be attached so TLS settings reach the LLM call.
    assert cfg.http_client is not None


def test_openai_omits_temperature_for_reasoning_model():
    provider = LLMProvider(
        {
            "provider": "openai",
            "llm_model": "o3-mini",
            "openai_api_key": "sk-test-not-a-real-key",
            "temperature": 0.7,
        }
    )
    cfg = provider.to_ag2_config()
    assert isinstance(cfg, OpenAICompletionsConfig)

    # Reasoning models reject a non-default temperature; it must be left unset.
    # AG2 represents "unset" with openai's Omit sentinel (not None, not a float).
    import openai

    assert not isinstance(cfg.temperature, float)
    assert isinstance(cfg.temperature, openai.Omit)


# ---------------------------------------------------------------------------
# Azure OpenAI provider — treated as OpenAI-standard base_url + key
# ---------------------------------------------------------------------------


def test_azure_openai_maps_deployment_and_endpoint():
    provider = LLMProvider(
        {
            "provider": "azure_openai",
            "llm_model": "my-deployment",
            "azure_api_key": "azure-test-not-a-real-key",
            "azure_endpoint": "https://example.openai.azure.com/",
            "temperature": 0.2,
        }
    )
    cfg = provider.to_ag2_config()

    assert isinstance(cfg, OpenAICompletionsConfig)
    # Deployment is the model; the configured endpoint is the base_url.
    assert cfg.model == "my-deployment"
    assert cfg.api_key == "azure-test-not-a-real-key"
    assert cfg.base_url == "https://example.openai.azure.com/"


def test_azure_openai_blank_endpoint_leaves_base_url_unset():
    provider = LLMProvider(
        {
            "provider": "azure_openai",
            "llm_model": "my-deployment",
            "azure_api_key": "azure-test-not-a-real-key",
            "azure_endpoint": "   ",
        }
    )
    cfg = provider.to_ag2_config()

    assert getattr(cfg, "base_url", None) in (None, "")


# ---------------------------------------------------------------------------
# Ollama provider — native OllamaConfig (host-based)
# ---------------------------------------------------------------------------


def _ollama_extra_healthy() -> bool:
    """True when ``ag2[ollama]`` is installed and importable (real dataclass)."""
    import dataclasses

    try:
        from ag2.config import OllamaConfig
    except Exception:
        return False
    return dataclasses.is_dataclass(OllamaConfig)


@pytest.mark.skipif(
    not _ollama_extra_healthy(),
    reason="ag2[ollama] extra not installed/healthy in this environment",
)
def test_ollama_builds_native_ollama_config():
    """Ollama uses AG2's native ``OllamaConfig`` with ``host=`` (no ``/v1``).

    Unlike OpenAI reasoning models, Ollama accepts a temperature, so it is
    forwarded.
    """
    from ag2.config import OllamaConfig

    provider = LLMProvider(
        {
            "provider": "ollama",
            "llm_model": "llama3.1",
            "ollama_base_url": "http://localhost:11434/",
            "temperature": 0.4,
        }
    )
    cfg = provider.to_ag2_config()

    assert isinstance(cfg, OllamaConfig)
    assert cfg.model == "llama3.1"
    # Native host is the server base URL - trailing slash trimmed, no /v1 suffix.
    assert cfg.host == "http://localhost:11434"
    assert cfg.http_client is not None
    assert cfg.temperature == 0.4


# ---------------------------------------------------------------------------
# Lifecycle / factory
# ---------------------------------------------------------------------------


def test_agent_http_client_is_cached():
    provider = LLMProvider(
        {
            "provider": "openai",
            "llm_model": "gpt-4o",
            "openai_api_key": "sk-test-not-a-real-key",
        }
    )
    first = provider.to_ag2_config()
    second = provider.to_ag2_config()
    assert isinstance(first, OpenAICompletionsConfig)
    assert isinstance(second, OpenAICompletionsConfig)
    # Repeated calls share one connection pool.
    assert first.http_client is second.http_client


def test_build_llm_provider_returns_retained_instance():
    provider = build_llm_provider(
        {
            "provider": "openai",
            "llm_model": "gpt-4o",
            "openai_api_key": "sk-test-not-a-real-key",
        }
    )
    assert isinstance(provider, LLMProvider)
    assert isinstance(provider.to_ag2_config(), OpenAICompletionsConfig)


# ---------------------------------------------------------------------------
# Registry dispatch
# ---------------------------------------------------------------------------


def test_registry_dispatch_maps_providers_to_adapters():
    from services.model_adapters import UnknownProviderError, get_adapter
    from services.model_adapters.anthropic import AnthropicAdapter
    from services.model_adapters.ollama import OllamaAdapter
    from services.model_adapters.openai import OpenAICompatibleAdapter

    assert isinstance(get_adapter("openai"), OpenAICompatibleAdapter)
    assert isinstance(get_adapter("azure_openai"), OpenAICompatibleAdapter)
    assert isinstance(get_adapter("anthropic"), AnthropicAdapter)
    assert isinstance(get_adapter("ollama"), OllamaAdapter)

    with pytest.raises(UnknownProviderError):
        get_adapter("does-not-exist")


# ---------------------------------------------------------------------------
# Anthropic adapter
# ---------------------------------------------------------------------------


def test_anthropic_build_kwargs_maps_fields_and_omits_temperature():
    from services.model_adapters.anthropic import _build_kwargs

    sentinel_client = object()
    kwargs = _build_kwargs(
        {
            "provider": "anthropic",
            "llm_model": "claude-x",
            "anthropic_api_key": "anthropic-test-not-a-real-key",
            "anthropic_base_url": "https://example.anthropic.test",
            "anthropic_max_tokens": 1024,
            # A temperature is present in config but must NOT be forwarded.
            "temperature": 0.5,
        },
        sentinel_client,
    )

    assert kwargs["model"] == "claude-x"
    assert kwargs["api_key"] == "anthropic-test-not-a-real-key"
    assert kwargs["base_url"] == "https://example.anthropic.test"
    assert kwargs["max_tokens"] == 1024
    assert kwargs["http_client"] is sentinel_client
    # The whole point of a separate adapter: Anthropic rejects temperature.
    assert "temperature" not in kwargs
    assert "top_p" not in kwargs
    assert "top_k" not in kwargs


def test_anthropic_build_kwargs_coerces_string_max_tokens():
    from services.model_adapters.anthropic import _build_kwargs

    kwargs = _build_kwargs(
        {"provider": "anthropic", "anthropic_max_tokens": "2048"},
        object(),
    )
    assert kwargs["max_tokens"] == 2048


def test_anthropic_build_kwargs_blank_base_url_is_omitted():
    from services.model_adapters.anthropic import _build_kwargs

    kwargs = _build_kwargs(
        {"provider": "anthropic", "anthropic_base_url": "   "},
        object(),
    )
    assert "base_url" not in kwargs


def _anthropic_extra_healthy() -> bool:
    """True when ``ag2[anthropic]`` is installed and importable (real dataclass).

    When the extra is absent or version-skewed, AG2 exports a
    ``missing_optional_dependency`` placeholder (not a dataclass), so the live
    construction test below skips instead of failing.
    """
    import dataclasses

    try:
        from ag2.config import AnthropicConfig
    except Exception:
        return False
    return dataclasses.is_dataclass(AnthropicConfig)


@pytest.mark.skipif(
    not _anthropic_extra_healthy(),
    reason="ag2[anthropic] extra not installed/healthy in this environment",
)
def test_anthropic_live_construction_real_config():
    """Real ``AnthropicConfig`` construction when the vendor extra is present.

    Complements the monkeypatched mapping test: this exercises the actual AG2
    typed config, confirming the fields land and ``temperature`` is unset.
    """
    from ag2.config import AnthropicConfig

    provider = LLMProvider(
        {
            "provider": "anthropic",
            "llm_model": "claude-sonnet-4-5",
            "anthropic_api_key": "sk-ant-not-a-real-key",
            "anthropic_base_url": "https://example.anthropic.test",
            "anthropic_max_tokens": 2048,
            "temperature": 0.7,
        }
    )
    cfg = provider.to_ag2_config()

    assert isinstance(cfg, AnthropicConfig)
    assert cfg.model == "claude-sonnet-4-5"
    assert cfg.max_tokens == 2048
    assert bool(cfg.api_key) is True
    assert cfg.base_url == "https://example.anthropic.test"
    assert cfg.http_client is not None
    # The adapter must not forward temperature even when config carries one.
    assert cfg.temperature is None


def test_anthropic_to_ag2_config_constructs_without_temperature(monkeypatch):
    """End-to-end dispatch: coordinator -> Anthropic adapter -> AnthropicConfig.

    ``AnthropicConfig`` is monkeypatched with a recorder so this runs without a
    working ``anthropic`` vendor library and asserts no ``temperature`` reaches
    the vendor config.
    """
    import ag2.config as agc

    captured: dict = {}

    class _RecorderAnthropicConfig:
        def __init__(self, **kw):
            captured.update(kw)

    monkeypatch.setattr(agc, "AnthropicConfig", _RecorderAnthropicConfig, raising=False)

    provider = LLMProvider(
        {
            "provider": "anthropic",
            "llm_model": "claude-x",
            "anthropic_api_key": "anthropic-test-not-a-real-key",
            "anthropic_max_tokens": 4096,
            "temperature": 0.7,
        }
    )
    cfg = provider.to_ag2_config()

    assert isinstance(cfg, _RecorderAnthropicConfig)
    assert captured["model"] == "claude-x"
    assert captured["http_client"] is not None
    assert "temperature" not in captured


# ---------------------------------------------------------------------------
# Generic `llm_model` resolution + legacy-key hard cut
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "provider, legacy_key",
    [
        ("openai", "openai_model"),
        ("azure_openai", "azure_deployment"),
        ("ollama", "ollama_model"),
        ("anthropic", "anthropic_model"),
    ],
)
def test_legacy_model_key_rejected_on_construction(provider, legacy_key):
    """Each legacy provider-specific model key is a hard error naming llm_model."""
    with pytest.raises(ValueError, match="llm_model"):
        LLMProvider({"provider": provider, legacy_key: "some-model"})


def test_mismatched_legacy_key_rejected():
    """A legacy key that does not match the active provider also raises.

    This is the old "silent wrong model" hazard turned into a loud failure.
    """
    with pytest.raises(ValueError, match="llm_model"):
        LLMProvider({"provider": "openai", "anthropic_model": "claude-x"})


def test_merge_env_credentials_rejects_legacy_model_key():
    with pytest.raises(ValueError, match="llm_model"):
        merge_env_credentials({"provider": "openai", "openai_model": "gpt-4o"}, env={})


@pytest.mark.parametrize(
    "provider, env_key, env_val",
    [
        ("openai", "OPENAI_MODEL", "gpt-env"),
        ("azure_openai", "AZURE_OPENAI_DEPLOYMENT", "deploy-env"),
        ("ollama", "OLLAMA_MODEL", "llama-env"),
        ("anthropic", "ANTHROPIC_MODEL", "claude-env"),
    ],
)
def test_env_supplies_llm_model_when_yaml_omits_it(provider, env_key, env_val):
    """Provider-scoped env var is the fallback when the YAML omits llm_model."""
    merged = merge_env_credentials({"provider": provider}, env={env_key: env_val})
    assert merged["llm_model"] == env_val


def test_yaml_llm_model_beats_env():
    merged = merge_env_credentials(
        {"provider": "openai", "llm_model": "gpt-yaml"},
        env={"OPENAI_MODEL": "gpt-env"},
    )
    assert merged["llm_model"] == "gpt-yaml"


@pytest.mark.parametrize("provider", ["openai", "azure_openai", "ollama", "anthropic"])
def test_provider_default_when_model_absent(provider):
    """With neither llm_model nor an env var, the provider default is used."""
    merged = merge_env_credentials({"provider": provider}, env={})
    assert merged["llm_model"] == DEFAULT_MODELS[provider]
