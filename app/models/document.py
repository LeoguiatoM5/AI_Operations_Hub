"""Modelo de documento da base de conhecimento.

Guarda apenas metadados: o texto dividido em pedacos vive no banco vetorial. Esta tabela
existe para responder "o que foi ingerido, quando, com que modelo de embedding e em que
estado" -- perguntas que o banco vetorial nao responde bem.
"""

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import UtcDateTime, utcnow
from app.models.enums import DocumentStatus


class Document(Base):
    """Um arquivo ingerido na base de conhecimento."""

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid4().hex)

    filename: Mapped[str] = mapped_column(String(255))
    extension: Mapped[str] = mapped_column(String(16))
    size_bytes: Mapped[int] = mapped_column()
    char_count: Mapped[int] = mapped_column(default=0)

    #: SHA-256 do conteudo bruto. Unico: reenviar o mesmo arquivo nao gera um segundo
    #: documento, o que duplicaria trechos no indice e degradaria a busca em silencio.
    content_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    status: Mapped[DocumentStatus] = mapped_column(
        String(16), default=DocumentStatus.PENDING, index=True
    )
    chunk_count: Mapped[int] = mapped_column(default=0)

    #: Trechos indexados com um modelo nao sao comparaveis com os de outro. Guardar qual
    #: foi usado e o que permite detectar uma base misturada antes que ela produza
    #: resultados sem sentido.
    embedding_provider: Mapped[str | None] = mapped_column(String(32))
    embedding_model: Mapped[str | None] = mapped_column(String(64))

    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String)

    doc_metadata: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON)

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, index=True)
    indexed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    def __repr__(self) -> str:
        return f"<Document id={self.id!r} filename={self.filename!r} status={self.status!r}>"
