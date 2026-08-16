"""Testes da recuperacao de contexto e do corte de relevancia."""

import pytest

from app.rag.base import Chunk
from app.rag.embeddings import FakeEmbeddingProvider
from app.rag.memory_store import InMemoryVectorStore
from app.rag.retriever import Retriever

BASE = {
    "reembolso": "Politica de reembolso: solicitacoes em ate 30 dias corridos.",
    "ferias": "Politica de ferias: periodo aquisitivo de doze meses de trabalho.",
    "seguranca": "Seguranca: senhas corporativas expiram a cada noventa dias.",
}


@pytest.fixture
async def store() -> InMemoryVectorStore:
    embedder = FakeEmbeddingProvider()
    store = InMemoryVectorStore()
    chunks = [
        Chunk(
            id=nome,
            document_id=f"doc-{nome}",
            text=texto,
            index=0,
            metadata={"filename": f"{nome}.md"},
        )
        for nome, texto in BASE.items()
    ]
    vetores = (await embedder.embed_documents([c.text for c in chunks])).vectors
    await store.add_chunks(chunks, vetores)
    return store


@pytest.fixture
def embedder() -> FakeEmbeddingProvider:
    return FakeEmbeddingProvider()


async def test_finds_relevant_context(
    store: InMemoryVectorStore, embedder: FakeEmbeddingProvider
) -> None:
    retriever = Retriever(embedder, store)

    resultado = await retriever.retrieve("qual o prazo para solicitar reembolso?")

    assert resultado.has_context
    assert resultado.hits[0].document_id == "doc-reembolso"
    assert resultado.best_score > 0


async def test_uses_the_floor_declared_by_the_provider(
    store: InMemoryVectorStore, embedder: FakeEmbeddingProvider
) -> None:
    """A escala de similaridade depende do modelo: quem sabe o corte e o provedor."""
    retriever = Retriever(embedder, store)

    assert retriever.min_score == embedder.min_relevant_score


async def test_explicit_floor_overrides_the_provider(
    store: InMemoryVectorStore, embedder: FakeEmbeddingProvider
) -> None:
    retriever = Retriever(embedder, store, min_score=0.9)

    assert retriever.min_score == 0.9


async def test_high_floor_reports_no_context(
    store: InMemoryVectorStore, embedder: FakeEmbeddingProvider
) -> None:
    """Sem contexto e um resultado valido -- e diferente de "a base esta vazia"."""
    retriever = Retriever(embedder, store, min_score=0.99)

    resultado = await retriever.retrieve("qual o prazo para solicitar reembolso?")

    assert not resultado.has_context
    assert resultado.discarded, "os trechos descartados precisam ficar visiveis"


async def test_discarded_hits_are_kept_for_diagnosis(
    store: InMemoryVectorStore, embedder: FakeEmbeddingProvider
) -> None:
    """ "Nao ha nada na base" e "ha algo, mas fraco" pedem acoes diferentes."""
    retriever = Retriever(embedder, store, min_score=0.99)

    resultado = await retriever.retrieve("reembolso")

    assert resultado.discarded[0].score < resultado.min_score


async def test_unrelated_question_finds_nothing_relevant(
    store: InMemoryVectorStore, embedder: FakeEmbeddingProvider
) -> None:
    retriever = Retriever(embedder, store, min_score=0.1)

    resultado = await retriever.retrieve("qual a receita de bolo de cenoura com chocolate?")

    assert not resultado.has_context


async def test_empty_index_returns_no_context(embedder: FakeEmbeddingProvider) -> None:
    retriever = Retriever(embedder, InMemoryVectorStore())

    resultado = await retriever.retrieve("qualquer pergunta")

    assert not resultado.has_context
    assert not resultado.discarded


async def test_top_k_limits_the_context(
    store: InMemoryVectorStore, embedder: FakeEmbeddingProvider
) -> None:
    retriever = Retriever(embedder, store, top_k=1, min_score=0.0)

    resultado = await retriever.retrieve("politica")

    assert len(resultado.hits) == 1


async def test_can_restrict_to_specific_documents(
    store: InMemoryVectorStore, embedder: FakeEmbeddingProvider
) -> None:
    retriever = Retriever(embedder, store, min_score=0.0)

    resultado = await retriever.retrieve("politica", document_ids=["doc-ferias"])

    assert {hit.document_id for hit in resultado.hits} == {"doc-ferias"}


async def test_reports_embedding_cost(
    store: InMemoryVectorStore, embedder: FakeEmbeddingProvider
) -> None:
    """A vetorizacao da pergunta tambem custa: precisa entrar na conta da consulta."""
    retriever = Retriever(embedder, store)

    resultado = await retriever.retrieve("qual o prazo para reembolso?")

    assert resultado.embedding_tokens > 0
    assert resultado.embedding_cost_usd == 0.0  # provedor falso nao custa
