"""Acesso aos documentos da base de conhecimento."""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.types import utcnow
from app.models.document import Document
from app.models.enums import DocumentStatus


class DocumentRepository:
    """Operacoes de leitura e escrita sobre documentos."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------ escrita

    async def create(
        self,
        *,
        filename: str,
        extension: str,
        size_bytes: int,
        content_hash: str,
        char_count: int,
        metadata: dict[str, Any] | None = None,
    ) -> Document:
        document = Document(
            filename=filename,
            extension=extension,
            size_bytes=size_bytes,
            content_hash=content_hash,
            char_count=char_count,
            doc_metadata=metadata,
            status=DocumentStatus.PENDING,
        )
        self._session.add(document)
        await self._session.flush()
        return document

    async def mark_processing(self, document: Document) -> Document:
        document.status = DocumentStatus.PROCESSING
        await self._session.flush()
        return document

    async def mark_indexed(
        self,
        document: Document,
        *,
        chunk_count: int,
        embedding_provider: str,
        embedding_model: str,
    ) -> Document:
        document.status = DocumentStatus.INDEXED
        document.chunk_count = chunk_count
        document.embedding_provider = embedding_provider
        document.embedding_model = embedding_model
        document.indexed_at = utcnow()
        document.error_code = None
        document.error_message = None
        await self._session.flush()
        return document

    async def mark_failed(
        self, document: Document, *, error_code: str, error_message: str
    ) -> Document:
        document.status = DocumentStatus.FAILED
        document.chunk_count = 0
        document.error_code = error_code
        document.error_message = error_message
        await self._session.flush()
        return document

    async def delete(self, document: Document) -> None:
        await self._session.delete(document)
        await self._session.flush()

    async def commit(self) -> None:
        """Confirma a transacao atual.

        Mesmo motivo do repositorio de execucoes: o registro de uma falha de ingestao
        precisa sobreviver ao rollback disparado pela propria falha.
        """
        await self._session.commit()

    # ------------------------------------------------------------------ leitura

    async def get(self, document_id: str) -> Document | None:
        return await self._session.get(Document, document_id)

    async def get_by_hash(self, content_hash: str) -> Document | None:
        """Busca por conteudo, nao por nome.

        Dois envios do mesmo arquivo com nomes diferentes sao o mesmo documento; dois
        arquivos de mesmo nome com conteudo diferente nao sao.
        """
        statement = select(Document).where(Document.content_hash == content_hash)
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def list(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        status: DocumentStatus | None = None,
    ) -> Sequence[Document]:
        statement = select(Document).order_by(Document.created_at.desc())
        if status is not None:
            statement = statement.where(Document.status == status)
        statement = statement.limit(limit).offset(offset)
        return (await self._session.execute(statement)).scalars().all()

    async def count(self, *, status: DocumentStatus | None = None) -> int:
        statement = select(func.count()).select_from(Document)
        if status is not None:
            statement = statement.where(Document.status == status)
        return int((await self._session.execute(statement)).scalar_one())

    async def total_chunks(self) -> int:
        statement = select(func.coalesce(func.sum(Document.chunk_count), 0)).where(
            Document.status == DocumentStatus.INDEXED
        )
        return int((await self._session.execute(statement)).scalar_one())
