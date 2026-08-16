"""Modelos de execucao.

Duas tabelas, nao uma. `Execution` e o pedido do usuario; `AgentExecution` e cada passo
dado por um agente dentro dele. Um unico pedido gera varias chamadas de LLM, e achatar
tudo em uma linha destruiria justamente a informacao que da valor ao projeto: a cadeia
de decisoes, com custo e latencia por etapa.
"""

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import UtcDateTime, utcnow
from app.models.enums import ExecutionStatus


def new_id() -> str:
    """Identificador opaco, gerado pela aplicacao.

    Gerado do lado da aplicacao (e nao pelo banco) para que o id exista antes do commit:
    ele ja pode aparecer em logs e ser devolvido ao cliente enquanto a transacao corre.
    """
    return uuid4().hex


class Execution(Base):
    """Um pedido do usuario, do recebimento a resposta final."""

    __tablename__ = "executions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)

    # Amarra a execucao aos logs do request que a originou.
    correlation_id: Mapped[str | None] = mapped_column(String(64), index=True)

    request_text: Mapped[str] = mapped_column(String)
    status: Mapped[ExecutionStatus] = mapped_column(
        String(32), default=ExecutionStatus.PENDING, index=True
    )

    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String)

    # Agregados dos passos filhos, mantidos aqui para evitar somar N linhas em toda
    # listagem. Sao dados derivados: recalculaveis a partir de agent_executions.
    total_prompt_tokens: Mapped[int] = mapped_column(default=0)
    total_completion_tokens: Mapped[int] = mapped_column(default=0)
    total_cost_usd: Mapped[float] = mapped_column(default=0.0)
    duration_ms: Mapped[float | None] = mapped_column()

    #: Preenchido pelo AI Quality Gateway (V5).
    quality_score: Mapped[float | None] = mapped_column()

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    agent_executions: Mapped[list["AgentExecution"]] = relationship(
        back_populates="execution",
        cascade="all, delete-orphan",
        order_by="AgentExecution.sequence",
        # selectin evita o problema N+1: uma consulta extra traz os passos de todas as
        # execucoes carregadas, em vez de uma consulta por execucao.
        lazy="selectin",
    )

    @property
    def total_tokens(self) -> int:
        return self.total_prompt_tokens + self.total_completion_tokens

    def __repr__(self) -> str:
        return f"<Execution id={self.id!r} status={self.status!r}>"


class AgentExecution(Base):
    """Um passo executado por um agente dentro de uma execucao."""

    __tablename__ = "agent_executions"
    __table_args__ = (
        # A consulta mais frequente e "todos os passos desta execucao, em ordem".
        Index("ix_agent_executions_execution_id_sequence", "execution_id", "sequence"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    execution_id: Mapped[str] = mapped_column(
        ForeignKey("executions.id", ondelete="CASCADE"), index=True
    )

    #: Posicao do passo dentro da execucao, a partir de 1.
    sequence: Mapped[int] = mapped_column()

    agent: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    status: Mapped[ExecutionStatus] = mapped_column(String(32), default=ExecutionStatus.PENDING)

    input: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    provider: Mapped[str | None] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(64))
    prompt_tokens: Mapped[int] = mapped_column(default=0)
    completion_tokens: Mapped[int] = mapped_column(default=0)
    cost_usd: Mapped[float] = mapped_column(default=0.0)
    latency_ms: Mapped[float | None] = mapped_column()
    attempts: Mapped[int] = mapped_column(default=1)

    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String)

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    execution: Mapped[Execution] = relationship(back_populates="agent_executions")

    def __repr__(self) -> str:
        return f"<AgentExecution agent={self.agent!r} seq={self.sequence}>"
