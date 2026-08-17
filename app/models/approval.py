"""Modelo de aprovacao humana.

Uma linha aqui e a resposta a pergunta "quem autorizou esta acao, quando, e o que
exatamente foi autorizado?". E o unico registro do sistema que existe para ser lido por
uma pessoa depois do fato -- possivelmente numa auditoria, possivelmente porque algo deu
errado.

Por isso os argumentos sao gravados junto com a decisao, e nao apenas referenciados: o
que foi aprovado precisa ficar imutavel no registro. Se o payload vivesse so no estado do
grafo, "aprovei o envio para o canal X" viraria uma afirmacao impossivel de verificar.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import StrEnumType, UtcDateTime, utcnow
from app.models.enums import ApprovalStatus
from app.models.execution import Execution, new_id


class Approval(Base):
    """Uma acao de escrita aguardando (ou tendo recebido) decisao humana."""

    __tablename__ = "approvals"
    __table_args__ = (
        # A consulta que a tela de pendencias faz: "aprovacoes pendentes, mais antigas
        # primeiro". Sem o indice, ela varre a tabela inteira.
        Index("ix_approvals_status_created_at", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    execution_id: Mapped[str] = mapped_column(
        ForeignKey("executions.id", ondelete="CASCADE"), index=True
    )

    #: Nome da ferramenta no `ToolRegistry`. Guardado como texto, e nao como enum: o
    #: catalogo muda com o tempo, e um registro de auditoria precisa continuar legivel
    #: mesmo depois de a ferramenta deixar de existir.
    tool: Mapped[str] = mapped_column(String(64))
    arguments: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    #: Justificativa do agente para querer executar isso.
    reason: Mapped[str | None] = mapped_column(String)

    status: Mapped[ApprovalStatus] = mapped_column(
        StrEnumType(ApprovalStatus, length=16), default=ApprovalStatus.PENDING, index=True
    )

    #: Quem decidiu. Texto livre porque o projeto nao tem autenticacao (decisao de
    #: escopo): quando houver, este campo passa a ser preenchido pela identidade do
    #: request em vez de vir no corpo.
    decided_by: Mapped[str | None] = mapped_column(String(120))
    decision_reason: Mapped[str | None] = mapped_column(String)

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, index=True)
    decided_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    execution: Mapped[Execution] = relationship()

    @property
    def is_pending(self) -> bool:
        return self.status is ApprovalStatus.PENDING

    def __repr__(self) -> str:
        return f"<Approval id={self.id!r} tool={self.tool!r} status={self.status!r}>"
