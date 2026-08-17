"""Endpoint de execucao do workflow multiagente."""

from fastapi import APIRouter, status

from app.api.deps import CorrelationIdDep, WorkflowServiceDep
from app.api.responses import with_errors
from app.schemas.common import ErrorResponse
from app.schemas.workflow import AgentRunRequest, AgentRunResponse

router = APIRouter(prefix="/agents", tags=["agents"])


@router.post(
    "/run",
    response_model=AgentRunResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Executa o workflow multiagente",
    responses=with_errors(
        **{
            "502": {"model": ErrorResponse, "description": "Falha do provedor de LLM"},
            "504": {"model": ErrorResponse, "description": "Provedor nao respondeu a tempo"},
        }
    ),
)
async def run_agents(
    payload: AgentRunRequest,
    service: WorkflowServiceDep,
    correlation_id: CorrelationIdDep,
) -> AgentRunResponse:
    """Interpreta a solicitacao, aciona os agentes necessarios e consolida um relatorio.

    O caminho percorrido e decidido pelo plano do orquestrador, nao fixado em codigo.

    Falha de um agente **nao** interrompe a execucao: o fluxo segue com o que conseguiu e
    o problema aparece em `errors` e nas limitacoes do relatorio. Uma resposta parcial
    honesta vale mais que erro sem nada aproveitado. A excecao e o orquestrador -- sem
    plano nao ha o que executar, e a execucao termina como `failed`.

    Se o plano envolver uma acao que altera sistema externo, a execucao para em
    `waiting_approval` e devolve `pending_approval`. Nada foi executado ainda: o fluxo so
    continua apos `POST /approvals/{id}/approve`.
    """
    execution, state, approval = await service.run(
        task=payload.task, data=payload.data, correlation_id=correlation_id
    )
    return AgentRunResponse.from_execution(execution, state, approval)
