"""Construcao dos componentes de RAG a partir da configuracao."""

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.retry import RetryPolicy
from app.llm.exceptions import LLMConfigurationError
from app.rag.base import EmbeddingProvider, VectorStore
from app.rag.chroma_store import ChromaVectorStore
from app.rag.embeddings import (
    FakeEmbeddingProvider,
    OpenAIEmbeddingProvider,
    RetryingEmbeddingProvider,
)
from app.rag.memory_store import InMemoryVectorStore

logger = get_logger(__name__)


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    """Devolve o provedor configurado, ja com politica de retry."""
    provider: EmbeddingProvider
    match settings.embedding_provider:
        case "fake":
            provider = FakeEmbeddingProvider(dimensions=settings.embedding_dimensions_fake)
        case "openai":
            if settings.openai_api_key is None:
                raise LLMConfigurationError(
                    "EMBEDDING_PROVIDER=openai exige OPENAI_API_KEY definida.",
                    details={"provider": "openai"},
                )
            provider = OpenAIEmbeddingProvider(
                api_key=settings.openai_api_key.get_secret_value(),
                model=settings.embedding_model,
                dimensions=settings.embedding_dimensions,
                timeout_seconds=settings.llm_timeout_seconds,
            )
        case _:
            raise LLMConfigurationError(
                f"Provedor de embedding nao suportado: {settings.embedding_provider!r}."
            )

    logger.info(
        "embedding_provider_built",
        provider=provider.name,
        model=provider.model,
        dimensions=provider.dimensions,
    )
    return RetryingEmbeddingProvider(
        provider,
        RetryPolicy(
            max_attempts=settings.llm_max_attempts,
            base_delay_seconds=settings.llm_retry_base_delay_seconds,
        ),
    )


def build_vector_store(settings: Settings) -> VectorStore:
    """Devolve o banco vetorial configurado."""
    store: VectorStore
    match settings.vector_store:
        case "memory":
            store = InMemoryVectorStore()
        case "chroma":
            store = ChromaVectorStore(
                path=settings.chroma_path,
                collection_name=settings.chroma_collection,
            )
        case _:
            raise LLMConfigurationError(f"Banco vetorial nao suportado: {settings.vector_store!r}.")

    logger.info("vector_store_built", store=store.name)
    return store
