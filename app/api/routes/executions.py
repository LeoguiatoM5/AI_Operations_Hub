"""Endpoints de consulta ao historico de execucoes."""

from typing import Annotated

from fastapi import APIRouter, Path, Query

from app.api.deps import ExecutionRepositoryDep
from app.api.responses import with_errors
from app.core.exceptions import NotFoundError
from app.models.enums import ExecutionStatus
from app.schemas.common import ErrorResponse
from app.schemas.execution import (
    ExecutionDetailResponse,
    ExecutionListResponse,
    ExecutionSummaryResponse,
)

router = APIRouter(prefix="/executions", tags=["executions"])


@router.get(
    "",
    response_model=ExecutionListResponse,
    summary="Lista execucoes, da mais recente para a mais antiga",
    responses=with_errors(),
)
async def list_executions(
    repository: ExecutionRepositoryDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    status: Annotated[ExecutionStatus | None, Query()] = None,
) -> ExecutionListResponse:
    executions = await repository.list(limit=limit, offset=offset, status=status)
    return ExecutionListResponse(
        total=await repository.count(status=status),
        limit=limit,
        offset=offset,
        items=[ExecutionSummaryResponse.from_model(item) for item in executions],
    )


@router.get(
    "/{execution_id}",
    response_model=ExecutionDetailResponse,
    summary="Detalha uma execucao com toda a cadeia de agentes",
    responses=with_errors(
        **{"404": {"model": ErrorResponse, "description": "Execucao nao encontrada"}}
    ),
)
async def get_execution(
    repository: ExecutionRepositoryDep,
    execution_id: Annotated[str, Path(min_length=1, max_length=64)],
) -> ExecutionDetailResponse:
    execution = await repository.get(execution_id)
    if execution is None:
        raise NotFoundError("Execucao nao encontrada.", details={"execution_id": execution_id})
    return ExecutionDetailResponse.from_model(execution)
