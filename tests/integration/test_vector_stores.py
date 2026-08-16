"""Teste de contrato dos bancos vetoriais.

A mesma bateria roda contra as duas implementacoes. E o que prova, na pratica, que o
Protocol `VectorStore` e uma abstracao de verdade: se o Chroma divergir do
comportamento de referencia, um destes testes quebra.

Sem isso, "trocar de banco vetorial e so mudar a env var" seria uma afirmacao do README
sem nenhuma evidencia.
"""

from collections.abc import AsyncIterator, Callable
from pathlib import Path

import pytest

from app.rag.base import Chunk, VectorStore
from app.rag.chroma_store import ChromaVectorStore
from app.rag.embeddings import FakeEmbeddingProvider
from app.rag.memory_store import InMemoryVectorStore

TEXTOS = {
    "reembolso": "A politica de reembolso permite solicitacoes em ate 30 dias corridos.",
    "ferias": "O periodo aquisitivo de ferias corresponde a doze meses de trabalho.",
    "seguranca": "Senhas corporativas expiram a cada noventa dias e exigem dois fatores.",
}


@pytest.fixture(params=["memory", "chroma"])
async def store(request: pytest.FixtureRequest, temp_dir: Path) -> AsyncIterator[VectorStore]:
    if request.param == "memory":
        instance: VectorStore = InMemoryVectorStore()
    else:
        instance = ChromaVectorStore(path=str(temp_dir / "chroma"), collection_name="testes")
    yield instance
    await instance.aclose()


@pytest.fixture
def embedder() -> FakeEmbeddingProvider:
    return FakeEmbeddingProvider()


@pytest.fixture
def make_chunk() -> Callable[[str, str, str], Chunk]:
    def build(chunk_id: str, document_id: str, text: str) -> Chunk:
        return Chunk(
            id=chunk_id,
            document_id=document_id,
            text=text,
            index=0,
            metadata={"document_id": document_id, "titulo": chunk_id},
        )

    return build


async def _index_all(
    store: VectorStore,
    embedder: FakeEmbeddingProvider,
    make_chunk: Callable[[str, str, str], Chunk],
) -> None:
    chunks = [make_chunk(nome, f"doc-{nome}", texto) for nome, texto in TEXTOS.items()]
    vetores = (await embedder.embed_documents([c.text for c in chunks])).vectors
    await store.add_chunks(chunks, vetores)


async def test_starts_empty(store: VectorStore) -> None:
    assert await store.count() == 0


async def test_counts_indexed_chunks(
    store: VectorStore,
    embedder: FakeEmbeddingProvider,
    make_chunk: Callable[[str, str, str], Chunk],
) -> None:
    await _index_all(store, embedder, make_chunk)

    assert await store.count() == 3


async def test_search_on_empty_index_returns_nothing(
    store: VectorStore, embedder: FakeEmbeddingProvider
) -> None:
    vetor = (await embedder.embed_query("qualquer coisa")).vectors[0]

    assert await store.search(vetor) == []


async def test_finds_the_relevant_chunk(
    store: VectorStore,
    embedder: FakeEmbeddingProvider,
    make_chunk: Callable[[str, str, str], Chunk],
) -> None:
    await _index_all(store, embedder, make_chunk)
    vetor = (await embedder.embed_query("qual o prazo para pedir reembolso?")).vectors[0]

    hits = await store.search(vetor, top_k=3)

    assert hits[0].document_id == "doc-reembolso"


async def test_results_are_ordered_by_similarity(
    store: VectorStore,
    embedder: FakeEmbeddingProvider,
    make_chunk: Callable[[str, str, str], Chunk],
) -> None:
    """Score e SEMPRE similaridade: maior primeiro.

    O Chroma devolve distancia, em que menor e melhor. Se a conversao na fronteira
    falhar, esta ordenacao inverte.
    """
    await _index_all(store, embedder, make_chunk)
    vetor = (await embedder.embed_query("reembolso")).vectors[0]

    hits = await store.search(vetor, top_k=3)

    scores = [hit.score for hit in hits]
    assert scores == sorted(scores, reverse=True)
    assert all(0.0 <= score <= 1.0 for score in scores)


async def test_top_k_limits_the_result(
    store: VectorStore,
    embedder: FakeEmbeddingProvider,
    make_chunk: Callable[[str, str, str], Chunk],
) -> None:
    await _index_all(store, embedder, make_chunk)
    vetor = (await embedder.embed_query("prazo")).vectors[0]

    assert len(await store.search(vetor, top_k=1)) == 1
    assert len(await store.search(vetor, top_k=2)) == 2


async def test_can_restrict_the_search_to_specific_documents(
    store: VectorStore,
    embedder: FakeEmbeddingProvider,
    make_chunk: Callable[[str, str, str], Chunk],
) -> None:
    await _index_all(store, embedder, make_chunk)
    vetor = (await embedder.embed_query("reembolso")).vectors[0]

    hits = await store.search(vetor, top_k=3, document_ids=["doc-ferias"])

    assert {hit.document_id for hit in hits} == {"doc-ferias"}


async def test_metadata_survives_the_round_trip(
    store: VectorStore,
    embedder: FakeEmbeddingProvider,
    make_chunk: Callable[[str, str, str], Chunk],
) -> None:
    await _index_all(store, embedder, make_chunk)
    vetor = (await embedder.embed_query("reembolso")).vectors[0]

    hit = (await store.search(vetor, top_k=1))[0]

    assert hit.metadata["titulo"] == "reembolso"
    assert hit.text == TEXTOS["reembolso"]


async def test_deleting_a_document_removes_its_chunks(
    store: VectorStore,
    embedder: FakeEmbeddingProvider,
    make_chunk: Callable[[str, str, str], Chunk],
) -> None:
    await _index_all(store, embedder, make_chunk)

    removidos = await store.delete_document("doc-reembolso")

    assert removidos == 1
    assert await store.count() == 2


async def test_deleting_an_unknown_document_is_harmless(store: VectorStore) -> None:
    assert await store.delete_document("nao-existe") == 0


async def test_reset_empties_the_index(
    store: VectorStore,
    embedder: FakeEmbeddingProvider,
    make_chunk: Callable[[str, str, str], Chunk],
) -> None:
    await _index_all(store, embedder, make_chunk)

    await store.reset()

    assert await store.count() == 0


async def test_reindexing_the_same_chunk_does_not_duplicate(
    store: VectorStore,
    embedder: FakeEmbeddingProvider,
    make_chunk: Callable[[str, str, str], Chunk],
) -> None:
    """Reprocessar um documento precisa substituir, nao acumular copias."""
    chunk = make_chunk("reembolso", "doc-1", TEXTOS["reembolso"])
    vetor = (await embedder.embed_documents([chunk.text])).vectors

    await store.add_chunks([chunk], vetor)
    await store.add_chunks([chunk], vetor)

    assert await store.count() == 1


async def test_mismatched_input_lengths_are_rejected(
    store: VectorStore,
    embedder: FakeEmbeddingProvider,
    make_chunk: Callable[[str, str, str], Chunk],
) -> None:
    chunk = make_chunk("a", "doc-1", "texto")
    vetores = (await embedder.embed_documents(["um", "dois"])).vectors

    with pytest.raises(ValueError, match="incompativel"):
        await store.add_chunks([chunk], vetores)
