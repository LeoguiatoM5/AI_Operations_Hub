"""Schemas do fluxo de aprovacao humana."""

from datetime import datetime
from typing import Any, Self

from pydantic import BaseModel, Field

from app.models.approval import Approval

MAX_DECIDER_LENGTH = 120
MAX_REASON_LENGTH = 500


class ApprovalResponse(BaseModel):
    """Uma acao aguardando -- ou tendo recebido -- decisao humana."""

    id: str
    execution_id: str
    status: str
    tool: str
    arguments: dict[str, Any] = Field(
        default_factory=dict,
        description="Exatamente o que sera executado se aprovado. Confira antes de decidir.",
    )
    reason: str | None = Field(default=None, description="Justificativa do agente.")

    decided_by: str | None = None
    decision_reason: str | None = None
    created_at: datetime
    decided_at: datetime | None = None

    @classmethod
    def from_model(cls, approval: Approval) -> Self:
        return cls(
            id=approval.id,
            execution_id=approval.execution_id,
            status=approval.status,
            tool=approval.tool,
            arguments=approval.arguments or {},
            reason=approval.reason,
            decided_by=approval.decided_by,
            decision_reason=approval.decision_reason,
            created_at=approval.created_at,
            decided_at=approval.decided_at,
        )


class ApprovalListResponse(BaseModel):
    """Fila de pendencias."""

    total: int
    limit: int
    offset: int
    items: list[ApprovalResponse] = Field(default_factory=list)


class ApprovalDecisionRequest(BaseModel):
    """A decisao de uma pessoa sobre uma acao pendente."""

    decided_by: str = Field(
        min_length=1,
        max_length=MAX_DECIDER_LENGTH,
        description="Quem esta decidindo. Obrigatorio: uma autorizacao sem autor nao e auditavel.",
        examples=["leonardo.guiato"],
    )
    reason: str | None = Field(
        default=None,
        max_length=MAX_REASON_LENGTH,
        description="Por que aprovou ou recusou. Vira parte do registro permanente.",
    )
