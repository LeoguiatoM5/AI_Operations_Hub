"""Ingestao de documentos na base de conhecimento.

O ponto delicado desta camada e que ela escreve em DOIS sistemas sem transacao comum:
o banco relacional (metadados) e o banco vetorial (trechos). Nao existe rollback que
desfaca os dois juntos.

A ordem e escolhida para que qualquer interrupcao deixe um rastro interpretavel:

    registra como `pending`
        -> `processing`            (a partir daqui, ha intencao declarada de indexar)
        -> grava vetores
        -> `indexed`               (so agora o documento participa da busca)

Se algo falhar no meio, os vetores parciais sao removidos e o documento fica `failed`.
Um documento parado em `processing` e evidencia de processo interrompido -- nao misterio.
"""

import hashlib

from app.core.exceptions import AIHubError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.models.document import Document
from app.models.enums import DocumentStatus
from app.rag.base import EmbeddingProvider, VectorStore
from app.rag.chunking import build_chunks
from app.rag.loaders import UnsupportedDocumentError, extract_text, normalize_extension
from app.repositories.document_repository import DocumentRepository

logger = get_logger(__name__)


class DocumentTooLargeError(AIHubError):
    """Arquivo acima do limite configurado."""

    code = "document_too_large"
    http_status = 413
    default_message = "Arquivo acima do tamanho maximo permitido."


class DuplicateDocumentError(AIHubError):
    """Documento com conteudo identico ja ingerido."""

    code = "duplicate_document"
    http_status = 409
    default_message = "Este arquivo ja foi ingerido."


class DocumentService:
    """Coordena extracao, divisao, vetorizacao e indexacao."""

    def __init__(
        self,
        repository: DocumentRepository,
        embedder: EmbeddingProvider,
        vector_store: VectorStore,
        *,
        chunk_size: int,
        chunk_overlap: int,
        max_size_bytes: int,
    ) -> None:
        self._repository = repository
        self._embedder = embedder
        self._store = vector_store
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._max_size_bytes = max_size_bytes

    async def ingest(self, content: bytes, *, filename: str) -> Document:
        """Ingere um arquivo e devolve o documento resultante.

        Raises:
            DocumentTooLargeError: acima do limite configurado.
            DuplicateDocumentError: conteudo identico ja ingerido.
            UnsupportedDocumentError: extensao fora da lista suportada.
            DocumentExtractionError / EmptyDocumentError: arquivo ilegivel ou sem texto.
        """
        self._validate_size(len(content))
        extension = normalize_extension(filename)
        if not filename.strip():
            raise ValidationError("O arquivo precisa ter um nome.")

        content_hash = hashlib.sha256(content).hexdigest()
        existing = await self._repository.get_by_hash(content_hash)
        if existing is not None:
            raise DuplicateDocumentError(
                f"Conteudo identico ja ingerido como {existing.filename!r}.",
                details={"document_id": existing.id, "filename": existing.filename},
            )

        # A extracao acontece ANTES de qualquer gravacao: nao faz sentido registrar um
        # documento que nem chegou a produzir texto.
        extracted = extract_text(content, filename=filename)

        document = await self._repository.create(
            filename=filename,
            extension=extension,
            size_bytes=len(content),
            content_hash=content_hash,
            char_count=len(extracted.text),
            metadata=extracted.metadata,
        )
        logger.info("document_registered", document_id=document.id, filename=filename)

        try:
            chunk_count = await self._index(document, extracted.text)
        except Exception as error:
            await self._rollback_index(document, error)
            raise

        await self._repository.mark_indexed(
            document,
            chunk_count=chunk_count,
            embedding_provider=self._embedder.name,
            embedding_model=self._embedder.model,
        )
        logger.info(
            "document_indexed",
            document_id=document.id,
            chunks=chunk_count,
            embedding_model=self._embedder.model,
        )
        return document

    async def delete(self, document_id: str) -> Document:
        """Remove o documento e todos os seus trechos do indice."""
        document = await self._repository.get(document_id)
        if document is None:
            raise NotFoundError("Documento nao encontrado.", details={"document_id": document_id})

        # Vetores primeiro: um documento sem metadados mas com vetores indexados
        # apareceria em buscas como um trecho sem origem identificavel.
        removed = await self._store.delete_document(document_id)
        await self._repository.delete(document)
        logger.info("document_deleted", document_id=document_id, chunks_removed=removed)
        return document

    # ------------------------------------------------------------------ internos

    def _validate_size(self, size_bytes: int) -> None:
        if size_bytes == 0:
            raise ValidationError("O arquivo esta vazio.")
        if size_bytes > self._max_size_bytes:
            raise DocumentTooLargeError(
                f"Arquivo de {size_bytes} bytes excede o limite de {self._max_size_bytes} bytes.",
                details={"size_bytes": size_bytes, "max_size_bytes": self._max_size_bytes},
            )

    async def _index(self, document: Document, text: str) -> int:
        await self._repository.mark_processing(document)

        chunks = build_chunks(
            text,
            document_id=document.id,
            chunk_size=self._chunk_size,
            chunk_overlap=self._chunk_overlap,
            metadata={"filename": document.filename, "extension": document.extension},
        )
        if not chunks:
            raise ValidationError("O documento nao produziu nenhum trecho indexavel.")

        vetores = await self._embedder.embed_documents([chunk.text for chunk in chunks])
        await self._store.add_chunks(chunks, vetores.vectors)
        return len(chunks)

    async def _rollback_index(self, document: Document, error: Exception) -> None:
        """Desfaz a indexacao parcial e registra a falha.

        Sem isso, uma falha na metade deixaria trechos orfaos no banco vetorial: eles
        continuariam aparecendo em buscas, apontando para um documento marcado como
        `failed`.
        """
        orfaos = await self._store.delete_document(document.id)

        code = error.code if isinstance(error, AIHubError) else "internal_error"
        message = error.message if isinstance(error, AIHubError) else str(error)

        await self._repository.mark_failed(document, error_code=code, error_message=message)
        await self._repository.commit()
        logger.warning(
            "document_ingestion_failed",
            document_id=document.id,
            error_code=code,
            orphan_chunks_removed=orfaos,
        )


__all__ = [
    "DocumentService",
    "DocumentStatus",
    "DocumentTooLargeError",
    "DuplicateDocumentError",
    "UnsupportedDocumentError",
]
