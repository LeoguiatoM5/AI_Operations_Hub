"""Testes da construcao dos componentes de RAG a partir da configuracao."""

from pathlib import Path

import pytest
from pydantic import SecretStr
from pydantic import ValidationError as PydanticValidationError

from app.core.config import Settings
from app.llm.exceptions import LLMConfigurationError
from app.rag.base import EmbeddingProvider, VectorStore
from app.rag.embeddings import RetryingEmbeddingProvider
from app.rag.factory import build_embedding_provider, build_vector_store
from app.rag.memory_store import InMemoryVectorStore


def make_settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


def test_default_embedding_provider_requires_no_api_key() -> None:
    provider = build_embedding_provider(make_settings())

    assert provider.name == "fake"
    assert isinstance(provider, EmbeddingProvider)
    assert isinstance(provider, RetryingEmbeddingProvider)


def test_openai_embeddings_without_key_fail_clearly() -> None:
    with pytest.raises(LLMConfigurationError, match="OPENAI_API_KEY"):
        build_embedding_provider(make_settings(embedding_provider="openai"))


def test_switching_embedding_provider_is_a_configuration_change() -> None:
    provider = build_embedding_provider(
        make_settings(embedding_provider="openai", openai_api_key=SecretStr("sk-teste"))
    )

    assert provider.name == "openai"
    assert provider.model == "text-embedding-3-small"
    assert provider.dimensions == 1536


def test_memory_store_can_be_selected() -> None:
    store = build_vector_store(make_settings(vector_store="memory"))

    assert isinstance(store, InMemoryVectorStore)
    assert isinstance(store, VectorStore)


def test_chroma_store_is_created_on_disk(temp_dir: Path) -> None:
    destino = temp_dir / "chroma"

    store = build_vector_store(make_settings(vector_store="chroma", chroma_path=str(destino)))

    assert store.name == "chroma"
    assert destino.exists()


def test_overlap_greater_than_chunk_size_is_rejected_on_startup() -> None:
    """Configuracao incoerente derruba a aplicacao antes de indexar qualquer coisa."""
    with pytest.raises(PydanticValidationError, match="CHUNK_OVERLAP"):
        make_settings(chunk_size=200, chunk_overlap=200)
