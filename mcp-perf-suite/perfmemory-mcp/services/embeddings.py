import logging
from typing import List, Optional

log = logging.getLogger(__name__)


class EmbeddingProvider:
    """Abstraction layer for embedding providers.

    Supports OpenAI, Azure OpenAI, and Ollama. The provider is selected
    based on the config dict passed at initialization. All providers expose
    the same interface: embed(text) -> list[float].

    HTTP clients are created lazily on first use and reused across calls
    to avoid spinning up a new connection pool / SSL context every time.

    Args:
        config: The "embedding" section of the config dict from utils/config.py.
    """

    def __init__(self, config: dict):
        self.provider = config.get("provider", "openai")
        self.config = config
        self._openai_client = None
        self._azure_client = None
        self._ollama_client = None

    async def embed(self, text: str) -> List[float]:
        """Convert text into a vector embedding.

        Args:
            text: The text to embed.

        Returns:
            List of floats representing the embedding vector.

        Raises:
            ValueError: If the configured provider is unknown.
            RuntimeError: If the embedding API call fails.
        """
        if self.provider == "openai":
            return await self._embed_openai(text)
        elif self.provider == "azure_openai":
            return await self._embed_azure_openai(text)
        elif self.provider == "ollama":
            return await self._embed_ollama(text)
        raise ValueError(f"Unknown embedding provider: {self.provider}")

    def get_model_name(self) -> str:
        """Return the model identifier for the current provider.

        Used to populate the embedding_model column in debug_attempts.
        """
        if self.provider == "openai":
            return self.config.get("openai_model", "text-embedding-3-small")
        elif self.provider == "azure_openai":
            return self.config.get("azure_deployment", "text-embedding-3-small")
        elif self.provider == "ollama":
            return self.config.get("ollama_model", "nomic-embed-text")
        return "unknown"

    async def close(self):
        """Release HTTP resources held by cached clients.

        Safe to call multiple times. Called during MCP server shutdown.
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

    def _get_openai_client(self):
        if self._openai_client is None:
            from openai import AsyncOpenAI
            self._openai_client = AsyncOpenAI(
                api_key=self.config.get("openai_api_key"),
            )
        return self._openai_client

    def _get_azure_client(self):
        if self._azure_client is None:
            from openai import AsyncAzureOpenAI
            self._azure_client = AsyncAzureOpenAI(
                api_key=self.config.get("azure_api_key"),
                azure_endpoint=self.config.get("azure_endpoint"),
                api_version=self.config.get("azure_api_version", "2024-02-15-preview"),
            )
        return self._azure_client

    def _get_ollama_client(self):
        if self._ollama_client is None:
            import httpx
            self._ollama_client = httpx.AsyncClient(timeout=30.0)
        return self._ollama_client

    async def _embed_openai(self, text: str) -> List[float]:
        """Generate embedding using the OpenAI API."""
        client = self._get_openai_client()
        model = self.config.get("openai_model", "text-embedding-3-small")

        try:
            response = await client.embeddings.create(input=text, model=model)
            return response.data[0].embedding
        except Exception as e:
            raise RuntimeError(f"OpenAI embedding failed: {e}") from e

    async def _embed_azure_openai(self, text: str) -> List[float]:
        """Generate embedding using Azure OpenAI.

        Requires AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, and
        AZURE_OPENAI_DEPLOYMENT to be configured.
        """
        client = self._get_azure_client()
        deployment = self.config.get("azure_deployment", "text-embedding-3-small")

        try:
            response = await client.embeddings.create(input=text, model=deployment)
            return response.data[0].embedding
        except Exception as e:
            raise RuntimeError(f"Azure OpenAI embedding failed: {e}") from e

    async def _embed_ollama(self, text: str) -> List[float]:
        """Generate embedding using a local Ollama instance.

        Requires Ollama running locally with the configured model pulled.
        """
        client = self._get_ollama_client()
        base_url = self.config.get("ollama_base_url", "http://localhost:11434")
        model = self.config.get("ollama_model", "nomic-embed-text")

        try:
            response = await client.post(
                f"{base_url}/api/embeddings",
                json={"model": model, "prompt": text},
            )
            response.raise_for_status()
            return response.json()["embedding"]
        except Exception as e:
            raise RuntimeError(f"Ollama embedding failed: {e}") from e
