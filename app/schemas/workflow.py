"""Schemas do endpoint de execucao de workflow."""

from typing import Any, Self

from pydantic import BaseModel, Field

from app.models.approval import Approval
from app.models.execution import Execution
from app.schemas.approval import ApprovalResponse
from app.schemas.execution import AgentStepResponse, UsageSummary
from app.workflows.state import WorkflowState

MAX_TASK_LENGTH = 8_000


class AgentRunRequest(BaseModel):
    """Solicitacao de execucao do workflow multiagente."""

    task: str = Field(
        min_length=3,
        max_length=MAX_TASK_LENGTH,
        description="O que deve ser feito, em linguagem natural.",
        examples=["Analise os chamados criticos de hoje e gere um relatorio."],
    )
    data: Any = Field(
        default=None,
        description="Dados estruturados a analisar. Sem eles, o agente de analise usa "
        "o resultado da pesquisa.",
    )


class WorkflowErrorResponse(BaseModel):
    """Falha de um agente que nao interrompeu a execucao."""

    agent: str
    code: str
    message: str


class AgentRunResponse(BaseModel):
    """Resultado do workflow, com a cadeia completa e o que falhou no caminho."""

    execution_id: str
    status: str
    task: str
    correlation_id: str | None = None

    agents_executed: list[str] = Field(default_factory=list)
    agents_skipped: list[str] = Field(
        default_factory=list, description="Pedidos pelo plano, ainda nao suportados."
    )
    errors: list[WorkflowErrorResponse] = Field(
        default_factory=list, description="Agentes que falharam sem interromper o fluxo."
    )

    plan: dict[str, Any] | None = None
    research: dict[str, Any] | None = None
    analysis: dict[str, Any] | None = None
    automation: dict[str, Any] | None = None
    report: dict[str, Any] | None = None

    #: Presente quando `status` e `waiting_approval`. Sem isto, o cliente receberia uma
    #: execucao parada e nenhuma indicacao de como faze-la seguir.
    pending_approval: ApprovalResponse | None = Field(
        default=None,
        description="Acao aguardando decisao humana. Decida em POST /approvals/{id}/approve.",
    )

    usage: UsageSummary
    duration_ms: float | None = None
    steps: list[AgentStepResponse] = Field(default_factory=list)

    @classmethod
    def from_execution(
        cls, execution: Execution, state: WorkflowState, approval: Approval | None = None
    ) -> Self:
        return cls(
            execution_id=execution.id,
            status=execution.status,
            task=execution.request_text,
            correlation_id=execution.correlation_id,
            agents_executed=list(state.get("completed", [])),
            agents_skipped=list(state.get("skipped_agents", [])),
            errors=[
                WorkflowErrorResponse(
                    agent=str(item.get("agent", "")),
                    code=str(item.get("code", "")),
                    message=str(item.get("message", "")),
                )
                for item in state.get("errors", [])
            ],
            plan=state.get("plan"),
            research=state.get("research"),
            analysis=state.get("analysis"),
            automation=state.get("automation"),
            report=state.get("report"),
            pending_approval=ApprovalResponse.from_model(approval) if approval else None,
            usage=UsageSummary(
                prompt_tokens=execution.total_prompt_tokens,
                completion_tokens=execution.total_completion_tokens,
                total_tokens=execution.total_tokens,
                cost_usd=execution.total_cost_usd,
            ),
            duration_ms=execution.duration_ms,
            steps=[AgentStepResponse.from_model(step) for step in execution.agent_executions],
        )
