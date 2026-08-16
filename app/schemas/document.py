"""Schemas dos endpoints de documentos."""

from datetime import datetime
from typing import Any, Self

from pydantic import BaseModel, Field

from app.models.document import Document
from app.models.enums import DocumentStatus


class DocumentResponse(BaseModel):
    """Metadados de um documento ingerido."""

    document_id: str
    filename: str
    extension: str
    status: DocumentStatus
    size_bytes: int
    char_count: int
    chunk_count: int
    content_hash: str
    embedding_provider: str | None = None
    embedding_model: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    metadata: dict[str, Any] | None = None
    created_at: datetime
    indexed_at: datetime | None = None

    @classmethod
    def from_model(cls, document: Document) -> Self:
        return cls(
            document_id=document.id,
            filename=document.filename,
            extension=document.extension,
            status=document.status,
            size_bytes=document.size_bytes,
            char_count=document.char_count,
            chunk_count=document.chunk_count,
            content_hash=document.content_hash,
            embedding_provider=document.embedding_provider,
            embedding_model=document.embedding_model,
            error_code=document.error_code,
            error_message=document.error_message,
            metadata=document.doc_metadata,
            created_at=document.created_at,
            indexed_at=document.indexed_at,
        )


class DocumentListResponse(BaseModel):
    """Pagina de documentos."""

    total: int
    limit: int
    offset: int
    indexed_chunks: int = Field(description="Total de trechos indexados na base.")
    items: list[DocumentResponse]


class DocumentDeletedResponse(BaseModel):
    """Confirmacao de remocao."""

    document_id: str
    filename: str
    chunks_removed: int
