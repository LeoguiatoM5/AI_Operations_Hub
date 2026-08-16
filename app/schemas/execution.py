"""Schemas de entrada e saida dos endpoints de execucao."""

from datetime import datetime
from typing import Any, Self

from pydantic import BaseModel, Field

from app.models.enums import ExecutionStatus
from app.models.execution import AgentExecution, Execution

#: Teto de tamanho da solicitacao. Limita custo por chamada e reduz a superficie de
#: prompt injection por texto longo colado de fonte externa.
MAX_MESSAGE_LENGTH = 8_000


class ChatRequest(BaseModel):
    """Solicitacao em linguagem natural."""

    message: str = Field(
        min_length=3,
        max_length=MAX_MESSAGE_LENGTH,
        description="O que deve ser feito, em linguagem natural.",
        examples=["Analise os chamados criticos de hoje e gere um relatorio."],
    )


class UsageSummary(BaseModel):
    """Consumo de uma execucao."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float


class AgentStepResponse(BaseModel):
    """Um passo da cadeia de agentes."""

    sequence: int
    agent: str
    action: str
    status: ExecutionStatus
    output: dict[str, Any] | None = None
    provider: str | None = None
    model: str | None = None
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    latency_ms: float | None = None
    attempts: int
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime

    @classmethod
    def from_model(cls, step: AgentExecution) -> Self:
        return cls(
            sequence=step.sequence,
            agent=step.agent,
            action=step.action,
            status=step.status,
            output=step.output,
            provider=step.provider,
            model=step.model,
            prompt_tokens=step.prompt_tokens,
            completion_tokens=step.completion_tokens,
            cost_usd=step.cost_usd,
            latency_ms=step.latency_ms,
            attempts=step.attempts,
            error_code=step.error_code,
            error_message=step.error_message,
            created_at=step.created_at,
        )


class ExecutionSummaryResponse(BaseModel):
    """Execucao sem a cadeia de passos, para listagens."""

    execution_id: str
    status: ExecutionStatus
    request_text: str
    correlation_id: str | None = None
    usage: UsageSummary
    duration_ms: float | None = None
    quality_score: float | None = None
    error_code: str | None = None
    created_at: datetime
    finished_at: datetime | None = None

    @classmethod
    def from_model(cls, execution: Execution) -> Self:
        return cls(
            execution_id=execution.id,
            status=execution.status,
            request_text=execution.request_text,
            correlation_id=execution.correlation_id,
            usage=UsageSummary(
                prompt_tokens=execution.total_prompt_tokens,
                completion_tokens=execution.total_completion_tokens,
                total_tokens=execution.total_tokens,
                cost_usd=execution.total_cost_usd,
            ),
            duration_ms=execution.duration_ms,
            quality_score=execution.quality_score,
            error_code=execution.error_code,
            created_at=execution.created_at,
            finished_at=execution.finished_at,
        )


class ExecutionDetailResponse(ExecutionSummaryResponse):
    """Execucao completa, com o resultado e a cadeia de agentes."""

    result: dict[str, Any] | None = None
    error_message: str | None = None
    steps: list[AgentStepResponse] = Field(default_factory=list)

    @classmethod
    def from_model(cls, execution: Execution) -> Self:
        summary = ExecutionSummaryResponse.from_model(execution)
        return cls(
            **summary.model_dump(),
            result=execution.result,
            error_message=execution.error_message,
            steps=[AgentStepResponse.from_model(step) for step in execution.agent_executions],
        )


class ExecutionListResponse(BaseModel):
    """Pagina de execucoes."""

    total: int
    limit: int
    offset: int
    items: list[ExecutionSummaryResponse]
