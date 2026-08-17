"""Decisao humana sobre acoes de escrita.

Este servico e a ponte entre uma pessoa e um grafo pausado. Ele faz tres coisas, nesta
ordem, e a ordem importa:

1. valida que a pendencia ainda pode ser decidida;
2. **grava a decisao e confirma a transacao**;
3. so entao retoma o grafo.

Gravar antes de retomar parece excesso de zelo, mas e o que garante que "quem autorizou o
que" sobreviva a um erro na execucao da acao. Na ordem inversa, uma falha na ferramenta
levaria embora tambem o registro da autorizacao -- e a pergunta "quem mandou fazer isso?"
ficaria sem resposta exatamente no caso em que ela e feita.
"""

from collections.abc import Sequence

from app.core.exceptions import AIHubError, NotFoundError
from app.core.logging import get_logger
from app.models.approval import Approval
from app.models.enums import ApprovalStatus
from app.models.execution import Execution
from app.repositories.approval_repository import ApprovalRepository
from app.repositories.execution_repository import ExecutionRepository
from app.services.workflow_service import WorkflowService
from app.workflows.state import WorkflowState

logger = get_logger(__name__)


class ApprovalAlreadyDecidedError(AIHubError):
    """Alguem ja decidiu esta pendencia.

    409, e nao 422: o pedido esta bem formado, mas conflita com o estado atual do
    recurso. A distincao importa para quem integra -- um `422` sugere corrigir o payload
    e tentar de novo, o que aqui nunca vai funcionar.
    """

    code = "approval_already_decided"
    http_status = 409
    default_message = "Esta aprovacao ja foi decidida."


class ApprovalService:
    """Lista pendencias e aplica decisoes humanas."""

    def __init__(
        self,
        approvals: ApprovalRepository,
        executions: ExecutionRepository,
        workflows: WorkflowService,
    ) -> None:
        self._approvals = approvals
        self._executions = executions
        self._workflows = workflows

    # ------------------------------------------------------------------ leitura

    async def list(
        self, *, limit: int = 50, offset: int = 0, status: ApprovalStatus | None = None
    ) -> tuple[Sequence[Approval], int]:
        return (
            await self._approvals.list(limit=limit, offset=offset, status=status),
            await self._approvals.count(status=status),
        )

    async def get(self, approval_id: str) -> Approval:
        approval = await self._approvals.get(approval_id)
        if approval is None:
            raise NotFoundError("Aprovacao nao encontrada.", details={"approval_id": approval_id})
        return approval

    # ------------------------------------------------------------------ decisao

    async def decide(
        self,
        approval_id: str,
        *,
        approved: bool,
        decided_by: str,
        reason: str | None = None,
    ) -> tuple[Approval, Execution, WorkflowState]:
        """Aplica a decisao e retoma a execucao do ponto em que ela parou."""
        approval = await self.get(approval_id)

        if approval.status.is_decided:
            raise ApprovalAlreadyDecidedError(
                f"Esta aprovacao ja foi {approval.status.value} por "
                f"{approval.decided_by or 'alguem'}.",
                details={
                    "approval_id": approval.id,
                    "status": approval.status.value,
                    "decided_by": approval.decided_by,
                },
            )

        execution = await self._executions.get(approval.execution_id)
        if execution is None:
            # Nao deveria acontecer -- ha chave estrangeira com CASCADE. Se acontecer, e
            # melhor dizer isso do que retomar um grafo sem dono.
            raise NotFoundError(
                "A execucao desta aprovacao nao existe mais.",
                details={"approval_id": approval.id, "execution_id": approval.execution_id},
            )

        await self._approvals.decide(
            approval,
            status=ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED,
            decided_by=decided_by,
            reason=reason,
        )
        # Confirmado ANTES da retomada: ver o cabecalho do modulo.
        await self._approvals.commit()

        logger.info(
            "approval_decided",
            approval_id=approval.id,
            execution_id=execution.id,
            tool=approval.tool,
            approved=approved,
            decided_by=decided_by,
        )

        state, _ = await self._workflows.resume(
            execution, approved=approved, decided_by=decided_by, reason=reason
        )
        return approval, execution, state
