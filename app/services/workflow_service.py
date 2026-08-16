"""Execucao de um workflow multiagente.

Camada sem FastAPI, como as demais: o servidor MCP (V6) vai reaproveita-la.

O contrato de erro aqui e diferente do `/chat`. La, uma falha de LLM significa que nada
foi produzido, e o status HTTP reflete isso. Aqui, o grafo continua apos a falha de um
agente e entrega o que conseguiu -- entao a execucao termina com `completed`, e o que
falhou aparece em `errors` e nas limitacoes do relatorio.

A excecao e o orquestrador: sem plano nao ha o que executar, e a execucao vira `failed`.
"""

from typing import Any, cast

from langgraph.checkpoint.base import BaseCheckpointSaver

from app.agents.analysis import AnalysisAgent
from app.agents.reporter import ReporterAgent
from app.agents.research import ResearchAgent
from app.agents.triage import TriageAgent
from app.core.logging import get_logger
from app.llm.base import LLMProvider
from app.models.enums import ExecutionStatus
from app.models.execution import Execution
from app.rag.retriever import Retriever
from app.repositories.execution_repository import ExecutionRepository
from app.workflows.graph import build_graph
from app.workflows.nodes import WorkflowNodes
from app.workflows.state import WorkflowState, initial_state

logger = get_logger(__name__)


class WorkflowService:
    """Roda o grafo de agentes e registra a execucao."""

    def __init__(
        self,
        repository: ExecutionRepository,
        provider: LLMProvider,
        retriever: Retriever,
        checkpointer: BaseCheckpointSaver[str] | None = None,
    ) -> None:
        self._repository = repository
        self._provider = provider
        self._retriever = retriever
        self._checkpointer = checkpointer

    async def run(
        self,
        *,
        task: str,
        data: Any = None,
        correlation_id: str | None = None,
    ) -> tuple[Execution, WorkflowState]:
        execution = await self._repository.create(
            request_text=task,
            correlation_id=correlation_id,
            status=ExecutionStatus.RUNNING,
        )

        nodes = WorkflowNodes(
            execution=execution,
            repository=self._repository,
            triage=TriageAgent(self._provider),
            research=ResearchAgent(self._provider),
            analysis=AnalysisAgent(self._provider),
            reporter=ReporterAgent(self._provider),
            retriever=self._retriever,
        )
        graph = build_graph(nodes, self._checkpointer)

        logger.info("workflow_started", execution_id=execution.id, has_data=data is not None)

        resultado = await graph.ainvoke(
            initial_state(execution_id=execution.id, request_text=task, input_data=data),
            # O thread_id amarra os checkpoints a esta execucao. E por ele que o V4 vai
            # retomar o grafo do ponto exato apos uma aprovacao humana.
            config={"configurable": {"thread_id": execution.id}},
        )
        # `ainvoke` devolve o estado como dicionario simples; o TypedDict e a nossa visao
        # tipada dele, nao uma classe em tempo de execucao.
        estado_final = cast(WorkflowState, resultado)

        await self._finish(execution, estado_final)
        return execution, estado_final

    async def _finish(self, execution: Execution, state: WorkflowState) -> None:
        falhou_no_plano = bool(state.get("fatal"))
        erros = state.get("errors", [])

        if falhou_no_plano:
            primeiro = erros[0] if erros else {"code": "internal_error", "message": "falha"}
            await self._repository.mark_finished(
                execution,
                status=ExecutionStatus.FAILED,
                error_code=str(primeiro.get("code")),
                error_message=str(primeiro.get("message")),
            )
        else:
            await self._repository.mark_finished(
                execution,
                status=ExecutionStatus.COMPLETED,
                result={
                    "plan": state.get("plan"),
                    "research": state.get("research"),
                    "analysis": state.get("analysis"),
                    "report": state.get("report"),
                    "errors": erros,
                    "skipped_agents": state.get("skipped_agents", []),
                },
            )

        await self._repository.load_steps(execution)
        logger.info(
            "workflow_finished",
            execution_id=execution.id,
            status=execution.status,
            agents=state.get("completed", []),
            errors=len(erros),
            cost_usd=execution.total_cost_usd,
            duration_ms=execution.duration_ms,
        )
