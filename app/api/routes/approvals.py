"""Endpoints de aprovacao humana.

Duas rotas de decisao, e nao uma com um booleano no corpo. `POST /approvals/{id}/approve`
e `POST /approvals/{id}/reject` dizem no proprio caminho o que esta acontecendo -- e
autorizar uma acao irreversivel nao e lugar para um campo que se erra por descuido.
"""

from typing import Annotated

from fastapi import APIRouter, Path, Query

from app.api.deps import ApprovalServiceDep
from app.api.responses import with_errors
from app.models.enums import ApprovalStatus
from app.schemas.approval import (
    ApprovalDecisionRequest,
    ApprovalListResponse,
    ApprovalResponse,
)
from app.schemas.common import ErrorResponse
from app.schemas.workflow import AgentRunResponse

router = APIRouter(prefix="/approvals", tags=["approvals"])

ApprovalIdPath = Annotated[str, Path(min_length=1, max_length=64)]


def _error(code: int, description: str) -> dict[str, dict[str, object]]:
    return {str(code): {"model": ErrorResponse, "description": description}}


@router.get(
    "",
    response_model=ApprovalListResponse,
    summary="Lista acoes aguardando decisao humana",
    responses=with_errors(),
)
async def list_approvals(
    service: ApprovalServiceDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    status_filter: Annotated[ApprovalStatus | None, Query(alias="status")] = None,
) -> ApprovalListResponse:
    """Fila de pendencias. Filtrando por `pending`, as mais ANTIGAS vem primeiro.

    A ordem se inverte em relacao a `/executions` de proposito: numa fila de espera, o que
    envelhece e o que corre risco de ser esquecido.
    """
    items, total = await service.list(limit=limit, offset=offset, status=status_filter)
    return ApprovalListResponse(
        total=total,
        limit=limit,
        offset=offset,
        items=[ApprovalResponse.from_model(item) for item in items],
    )


@router.get(
    "/{approval_id}",
    response_model=ApprovalResponse,
    summary="Detalha uma pendencia",
    responses=with_errors(**_error(404, "Aprovacao nao encontrada")),
)
async def get_approval(
    service: ApprovalServiceDep, approval_id: ApprovalIdPath
) -> ApprovalResponse:
    """Mostra exatamente o que sera executado, para conferencia antes de decidir."""
    return ApprovalResponse.from_model(await service.get(approval_id))


@router.post(
    "/{approval_id}/approve",
    response_model=AgentRunResponse,
    summary="Autoriza a acao e retoma a execucao",
    responses=with_errors(
        **_error(404, "Aprovacao nao encontrada"),
        **_error(409, "Aprovacao ja decidida"),
        **_error(502, "Falha ao executar a acao autorizada"),
    ),
)
async def approve(
    service: ApprovalServiceDep, approval_id: ApprovalIdPath, payload: ApprovalDecisionRequest
) -> AgentRunResponse:
    """Executa a acao e conclui a execucao a partir do ponto em que ela parou.

    Nenhum passo anterior e refeito: o grafo retoma do checkpoint, entao aprovar nao paga
    de novo os tokens da pesquisa, da analise nem do planejamento.
    """
    _approval, execution, state = await service.decide(
        approval_id, approved=True, decided_by=payload.decided_by, reason=payload.reason
    )
    return AgentRunResponse.from_execution(execution, state)


@router.post(
    "/{approval_id}/reject",
    response_model=AgentRunResponse,
    summary="Recusa a acao e conclui a execucao sem executa-la",
    responses=with_errors(
        **_error(404, "Aprovacao nao encontrada"),
        **_error(409, "Aprovacao ja decidida"),
    ),
)
async def reject(
    service: ApprovalServiceDep, approval_id: ApprovalIdPath, payload: ApprovalDecisionRequest
) -> AgentRunResponse:
    """Encerra a execucao sem executar a acao.

    Recusa nao e erro: a execucao termina como `completed`, e o relatorio final registra
    que a acao foi recusada e por quem. O sistema funcionou como deveria.
    """
    _approval, execution, state = await service.decide(
        approval_id, approved=False, decided_by=payload.decided_by, reason=payload.reason
    )
    return AgentRunResponse.from_execution(execution, state)
