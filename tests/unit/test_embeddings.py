"""Testes do provedor de embedding deterministico.

Ele nao existe apenas para "passar teste": precisa produzir similaridade lexical real,
senao os testes de RAG verificariam encanamento em vez de comportamento.
"""

import pytest

from app.rag.base import EmbeddingProvider
from app.rag.embeddings import FakeEmbeddingProvider
from app.rag.memory_store import cosine_similarity

REEMBOLSO = "A politica de reembolso permite solicitacoes em ate 30 dias corridos."
FERIAS = "O periodo aquisitivo de ferias e de doze meses de trabalho."


def test_satisfies_the_provider_protocol() -> None:
    assert isinstance(FakeEmbeddingProvider(), EmbeddingProvider)


async def test_vector_has_the_declared_dimensions() -> None:
    provider = FakeEmbeddingProvider(dimensions=128)

    resultado = await provider.embed_query("qualquer texto")

    assert provider.dimensions == 128
    assert len(resultado.vectors[0]) == 128


async def test_is_deterministic_across_instances() -> None:
    """Vetores gravados hoje precisam continuar compativeis com a busca de amanha.

    `hash()` de string em Python e aleatorizado por processo -- usa-lo aqui quebraria
    esta propriedade silenciosamente.
    """
    primeiro = (await FakeEmbeddingProvider().embed_query(REEMBOLSO)).vectors[0]
    segundo = (await FakeEmbeddingProvider().embed_query(REEMBOLSO)).vectors[0]

    assert primeiro == segundo


async def test_vectors_are_normalized() -> None:
    vetor = (await FakeEmbeddingProvider().embed_query(REEMBOLSO)).vectors[0]

    norma = sum(valor * valor for valor in vetor) ** 0.5
    assert norma == pytest.approx(1.0)


async def test_similar_texts_are_closer_than_unrelated_ones() -> None:
    """Esta e a propriedade que torna os testes de RAG significativos."""
    provider = FakeEmbeddingProvider()
    resultado = await provider.embed_documents(
        ["Qual e a politica de reembolso?", REEMBOLSO, FERIAS]
    )
    pergunta, sobre_reembolso, sobre_ferias = resultado.vectors

    assert cosine_similarity(pergunta, sobre_reembolso) > cosine_similarity(pergunta, sobre_ferias)


async def test_accents_do_not_split_the_vocabulary() -> None:
    """Sem normalizar acentuacao, "reembolso" e "reembôlso" seriam palavras distintas."""
    provider = FakeEmbeddingProvider()

    resultado = await provider.embed_documents(["reembolso ferias", "reembôlso férias"])

    assert cosine_similarity(*resultado.vectors) == pytest.approx(1.0)


async def test_empty_text_yields_a_zero_vector() -> None:
    vetor = (await FakeEmbeddingProvider().embed_query("!!! ???")).vectors[0]

    assert all(valor == 0.0 for valor in vetor)


async def test_batch_returns_one_vector_per_text() -> None:
    resultado = await FakeEmbeddingProvider().embed_documents([REEMBOLSO, FERIAS, "outro"])

    assert len(resultado.vectors) == 3
    assert resultado.usage.tokens > 0
    assert resultado.usage.cost_usd == 0.0
