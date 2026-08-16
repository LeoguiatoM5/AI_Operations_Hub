"""Testes da ingestao de documentos.

Cobrem sobretudo o ponto delicado da etapa: a consistencia entre dois sistemas que nao
compartilham transacao -- o banco relacional e o banco vetorial.
"""

from collections.abc import Sequence

import pytest

from app.core.exceptions import NotFoundError, ValidationError
from app.models.enums import DocumentStatus
from app.rag.base import EmbeddingResult
from app.rag.embeddings import FakeEmbeddingProvider
from app.rag.loaders import UnsupportedDocumentError
from app.rag.memory_store import InMemoryVectorStore
from app.repositories.document_repository import DocumentRepository
from app.services.document_service import (
    DocumentService,
    DocumentTooLargeError,
    DuplicateDocumentError,
)

POLITICA = (
    b"Politica de reembolso. Colaboradores podem solicitar reembolso de despesas em ate "
    b"30 dias corridos apos o gasto. A analise leva 5 dias uteis. Valores acima de mil "
    b"reais exigem aprovacao da diretoria financeira."
)


class BrokenEmbeddingProvider:
    """Provedor que falha ao vetorizar, para exercitar a limpeza de indexacao parcial."""

    name = "quebrado"
    model = "quebrado-1"
    dimensions = 8
    min_relevant_score = 0.0

    async def embed_documents(self, texts: Sequence[str]) -> EmbeddingResult:
        raise RuntimeError("provedor de embedding indisponivel")

    async def embed_query(self, text: str) -> EmbeddingResult:
        raise RuntimeError("provedor de embedding indisponivel")

    async def aclose(self) -> None:
        return None


# ---------------------------------------------------------------- caminho feliz


async def test_ingests_and_indexes(
    document_service: DocumentService, vector_store: InMemoryVectorStore
) -> None:
    document = await document_service.ingest(POLITICA, filename="politica.txt")

    assert document.status == DocumentStatus.INDEXED
    assert document.chunk_count > 0
    assert document.char_count == len(POLITICA.decode())
    assert document.indexed_at is not None
    assert await vector_store.count() == document.chunk_count


async def test_indexed_document_is_findable(
    document_service: DocumentService,
    vector_store: InMemoryVectorStore,
    embedder: FakeEmbeddingProvider,
) -> None:
    """O teste que importa: o documento entrou e a busca o encontra."""
    document = await document_service.ingest(POLITICA, filename="politica.txt")
    vetor = (await embedder.embed_query("prazo para solicitar reembolso")).vectors[0]

    hits = await vector_store.search(vetor, top_k=1)

    assert hits[0].document_id == document.id
    assert "reembolso" in hits[0].text


async def test_records_which_embedding_model_was_used(
    document_service: DocumentService,
) -> None:
    """Trechos de modelos diferentes nao sao comparaveis: e preciso saber qual foi."""
    document = await document_service.ingest(POLITICA, filename="politica.txt")

    assert document.embedding_provider == "fake"
    assert document.embedding_model == "fake-embedding-1"


async def test_chunk_metadata_carries_the_filename(
    document_service: DocumentService,
    vector_store: InMemoryVectorStore,
    embedder: FakeEmbeddingProvider,
) -> None:
    await document_service.ingest(POLITICA, filename="politica.txt")
    vetor = (await embedder.embed_query("reembolso")).vectors[0]

    hit = (await vector_store.search(vetor, top_k=1))[0]

    assert hit.metadata["filename"] == "politica.txt"


# ---------------------------------------------------------------- deduplicacao


async def test_identical_content_is_rejected(document_service: DocumentService) -> None:
    """Reindexar o mesmo conteudo duplicaria trechos e degradaria a busca em silencio."""
    primeiro = await document_service.ingest(POLITICA, filename="politica.txt")

    with pytest.raises(DuplicateDocumentError) as exc_info:
        await document_service.ingest(POLITICA, filename="copia-da-politica.txt")

    assert exc_info.value.details["document_id"] == primeiro.id


async def test_duplicate_check_uses_content_not_filename(
    document_service: DocumentService, vector_store: InMemoryVectorStore
) -> None:
    await document_service.ingest(POLITICA, filename="a.txt")
    await document_service.ingest(POLITICA + b" Revisao 2.", filename="a.txt")

    assert await vector_store.count() > 0


# ---------------------------------------------------------------- validacao


async def test_oversized_file_is_rejected_before_processing(
    document_service: DocumentService, documents: DocumentRepository
) -> None:
    with pytest.raises(DocumentTooLargeError):
        await document_service.ingest(b"x" * (64 * 1024 + 1), filename="grande.txt")

    assert await documents.count() == 0


async def test_empty_file_is_rejected(document_service: DocumentService) -> None:
    with pytest.raises(ValidationError):
        await document_service.ingest(b"", filename="vazio.txt")


async def test_unsupported_format_leaves_no_record(
    document_service: DocumentService, documents: DocumentRepository
) -> None:
    """A extracao acontece antes de gravar: nao se registra o que nem virou texto."""
    with pytest.raises(UnsupportedDocumentError):
        await document_service.ingest(b"conteudo", filename="planilha.xlsx")

    assert await documents.count() == 0


# ---------------------------------------------------------------- falha na indexacao


async def test_indexing_failure_marks_the_document_and_leaves_no_orphans(
    documents: DocumentRepository, vector_store: InMemoryVectorStore
) -> None:
    """Sem a limpeza, sobrariam trechos apontando para um documento marcado como falho."""
    service = DocumentService(
        documents,
        BrokenEmbeddingProvider(),
        vector_store,
        chunk_size=200,
        chunk_overlap=20,
        max_size_bytes=64 * 1024,
    )

    with pytest.raises(RuntimeError):
        await service.ingest(POLITICA, filename="politica.txt")

    registrados = await documents.list()
    assert len(registrados) == 1
    assert registrados[0].status == DocumentStatus.FAILED
    assert registrados[0].chunk_count == 0
    assert await vector_store.count() == 0


async def test_failed_document_survives_the_rollback(
    documents: DocumentRepository, vector_store: InMemoryVectorStore
) -> None:
    """Mesma licao do ED-025: a auditoria da falha nao pode morrer com a falha."""
    service = DocumentService(
        documents,
        BrokenEmbeddingProvider(),
        vector_store,
        chunk_size=200,
        chunk_overlap=20,
        max_size_bytes=64 * 1024,
    )

    with pytest.raises(RuntimeError):
        await service.ingest(POLITICA, filename="politica.txt")

    assert await documents.count(status=DocumentStatus.FAILED) == 1


# ---------------------------------------------------------------- remocao


async def test_delete_removes_document_and_chunks(
    document_service: DocumentService,
    documents: DocumentRepository,
    vector_store: InMemoryVectorStore,
) -> None:
    document = await document_service.ingest(POLITICA, filename="politica.txt")

    await document_service.delete(document.id)

    assert await documents.count() == 0
    assert await vector_store.count() == 0


async def test_delete_only_affects_the_target_document(
    document_service: DocumentService, vector_store: InMemoryVectorStore
) -> None:
    primeiro = await document_service.ingest(POLITICA, filename="a.txt")
    await document_service.ingest(
        b"Politica de ferias com doze meses aquisitivos.", filename="b.txt"
    )
    total = await vector_store.count()

    await document_service.delete(primeiro.id)

    assert 0 < await vector_store.count() < total


async def test_deleting_an_unknown_document_fails_clearly(
    document_service: DocumentService,
) -> None:
    with pytest.raises(NotFoundError):
        await document_service.delete("nao-existe")


async def test_content_can_be_reingested_after_deletion(
    document_service: DocumentService,
) -> None:
    """A deduplicacao nao pode impedir uma reingestao legitima apos remocao."""
    document = await document_service.ingest(POLITICA, filename="politica.txt")
    await document_service.delete(document.id)

    novo = await document_service.ingest(POLITICA, filename="politica.txt")

    assert novo.status == DocumentStatus.INDEXED
    assert novo.id != document.id
