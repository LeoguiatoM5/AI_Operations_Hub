"""Endpoint de solicitacao em linguagem natural."""

from fastapi import APIRouter, status

from app.api.deps import CorrelationIdDep, ExecutionServiceDep
from app.api.responses import with_errors
from app.schemas.common import ErrorResponse
from app.schemas.execution import ChatRequest, ExecutionDetailResponse

router = APIRouter(tags=["chat"])


@router.post(
    "/chat",
    response_model=ExecutionDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Processa uma solicitacao em linguagem natural",
    responses=with_errors(
        **{
            "429": {"model": ErrorResponse, "description": "Cota do provedor excedida"},
            "502": {"model": ErrorResponse, "description": "Falha do provedor de LLM"},
            "504": {
                "model": ErrorResponse,
                "description": "Provedor de LLM nao respondeu a tempo",
            },
        }
    ),
)
async def chat(
    payload: ChatRequest,
    service: ExecutionServiceDep,
    correlation_id: CorrelationIdDep,
) -> ExecutionDetailResponse:
    """Interpreta a solicitacao e devolve a execucao completa, com a cadeia de agentes.

    Falha do provedor de LLM devolve o status HTTP correspondente (502 ou 504), com o
    `execution_id` em `error.details` -- a execucao fica gravada como `failed` e pode ser
    inspecionada em `GET /executions/{id}`.
    """
    execution = await service.run(request_text=payload.message, correlation_id=correlation_id)
    return ExecutionDetailResponse.from_model(execution)
