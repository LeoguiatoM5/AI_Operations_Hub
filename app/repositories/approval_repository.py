"""Acesso as aprovacoes."""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.types import utcnow
from app.models.approval import Approval
from app.models.enums import ApprovalStatus


class ApprovalRepository:
    """Operacoes de leitura e escrita sobre aprovacoes."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        execution_id: str,
        tool: str,
        arguments: dict[str, Any],
        reason: str | None = None,
    ) -> Approval:
        approval = Approval(
            execution_id=execution_id,
            tool=tool,
            arguments=arguments,
            reason=reason,
            status=ApprovalStatus.PENDING,
        )
        self._session.add(approval)
        await self._session.flush()
        return approval

    async def decide(
        self,
        approval: Approval,
        *,
        status: ApprovalStatus,
        decided_by: str,
        reason: str | None = None,
    ) -> Approval:
        """Grava a decisao humana.

        Nao valida se ja havia decisao: essa regra e do servico, que conhece o fluxo do
        grafo. O repositorio guarda o que mandarem guardar.
        """
        approval.status = status
        approval.decided_by = decided_by
        approval.decision_reason = reason
        approval.decided_at = utcnow()
        await self._session.flush()
        return approval

    async def commit(self) -> None:
        """Confirma a transacao atual.

        Mesmo vazamento deliberado do `ExecutionRepository`, por um motivo parecido: a
        decisao humana precisa estar gravada ANTES de o grafo ser retomado. Se a retomada
        falhar, o registro de quem decidiu o que nao pode desaparecer junto.
        """
        await self._session.commit()

    async def get(self, approval_id: str) -> Approval | None:
        return await self._session.get(Approval, approval_id)

    async def get_pending_for_execution(self, execution_id: str) -> Approval | None:
        """Pendencia aberta de uma execucao, se houver.

        Usado para nao criar uma segunda pendencia para a mesma pausa: o grafo pode ser
        reinvocado, e duas linhas pendentes para a mesma acao dariam ao humano a
        impressao de que ha duas coisas a decidir.
        """
        statement = (
            select(Approval)
            .where(
                Approval.execution_id == execution_id,
                Approval.status == ApprovalStatus.PENDING,
            )
            .order_by(Approval.created_at.desc())
            .limit(1)
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def list(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        status: ApprovalStatus | None = None,
    ) -> Sequence[Approval]:
        """Lista aprovacoes. Pendentes primeiro pelas mais ANTIGAS.

        A ordem se inverte de proposito em relacao as execucoes: numa fila de espera, o
        que envelhece e o que corre risco de ser esquecido.
        """
        statement = select(Approval)
        if status is not None:
            statement = statement.where(Approval.status == status)

        ordem = (
            Approval.created_at.asc()
            if status is ApprovalStatus.PENDING
            else Approval.created_at.desc()
        )
        statement = statement.order_by(ordem).limit(limit).offset(offset)

        return (await self._session.execute(statement)).scalars().all()

    async def count(self, *, status: ApprovalStatus | None = None) -> int:
        statement = select(func.count()).select_from(Approval)
        if status is not None:
            statement = statement.where(Approval.status == status)
        return int((await self._session.execute(statement)).scalar_one())
