"""Execucao de um workflow multiagente.

Camada sem FastAPI, como as demais: o servidor MCP (V6) vai reaproveita-la.

O contrato de erro aqui e diferente do `/chat`. La, uma falha de LLM significa que nada
foi produzido, e o status HTTP reflete isso. Aqui, o grafo continua apos a falha de um
agente e entrega o que conseguiu -- entao a execucao termina com `completed`, e o que
falhou aparece em `errors` e nas limitacoes do relatorio.

A excecao e o orquestrador: sem plano nao ha o que executar, e a execucao vira `failed`.

**Terceiro desfecho, a partir do V4:** o grafo pode nao terminar. Se a acao planejada
altera sistema externo, ele pausa e a execucao fica em `waiting_approval` ate uma pessoa
decidir. Quem registra a pendencia e este servico, e nao o no que pausou -- o no
reexecuta na retomada, e criar a aprovacao la dentro geraria uma segunda linha para a
mesma decisao.
"""

from dataclasses import dataclass
from typing import Any, cast

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from app.agents.analysis import AnalysisAgent
from app.agents.automation import AutomationAgent
from app.agents.reporter import ReporterAgent
from app.agents.research import ResearchAgent
from app.agents.triage import TriageAgent
from app.core.logging import get_logger
from app.integrations.callback import NullPublisher, ResultPublisher
from app.llm.base import LLMProvider
from app.models.approval import Approval
from app.models.enums import ExecutionStatus
from app.models.execution import Execution
from app.quality.engine import QualityEngine
from app.rag.retriever import Retriever
from app.repositories.approval_repository import ApprovalRepository
from app.repositories.execution_repository import ExecutionRepository
from app.schemas.workflow import AgentRunResponse
from app.tools.registry import ToolRegistry
from app.workflows.graph import build_graph
from app.workflows.nodes import WorkflowNodes
from app.workflows.state import WorkflowState, initial_state

logger = get_logger(__name__)

#: Chave que o LangGraph adiciona ao resultado quando o grafo pausou. Nao faz parte do
#: `WorkflowState`: e um canal de controle da biblioteca, e por isso e removida antes de
#: o estado seguir para as camadas de cima.
INTERRUPT_KEY = "__interrupt__"


@dataclass(frozen=True)
class PendingAction:
    """A pausa que o grafo devolveu: o que se pretende fazer, e por que."""

    tool: str
    arguments: dict[str, Any]
    reason: str | None


class WorkflowService:
    """Roda o grafo de agentes e registra a execucao."""

    def __init__(
        self,
        repository: ExecutionRepository,
        provider: LLMProvider,
        retriever: Retriever,
        tools: ToolRegistry,
        approvals: ApprovalRepository,
        checkpointer: BaseCheckpointSaver[str] | None = None,
        publisher: ResultPublisher | None = None,
        quality: QualityEngine | None = None,
    ) -> None:
        self._repository = repository
        self._provider = provider
        self._retriever = retriever
        self._tools = tools
        self._approvals = approvals
        self._checkpointer = checkpointer
        self._publisher = publisher or NullPublisher()
        self._quality = quality

    # ------------------------------------------------------------------ execucao

    async def run(
        self,
        *,
        task: str,
        data: Any = None,
        correlation_id: str | None = None,
    ) -> tuple[Execution, WorkflowState, Approval | None]:
        """Executa o workflow. Devolve a aprovacao pendente se o grafo tiver pausado."""
        execution = await self._repository.create(
            request_text=task,
            correlation_id=correlation_id,
            status=ExecutionStatus.RUNNING,
        )

        graph = self._build(execution)
        logger.info("workflow_started", execution_id=execution.id, has_data=data is not None)

        bruto = await graph.ainvoke(
            initial_state(execution_id=execution.id, request_text=task, input_data=data),
            config=self._config(execution.id),
        )

        estado, pendencia = self._split(bruto)
        aprovacao = await self._finish(execution, estado, pendencia)
        return execution, estado, aprovacao

    async def resume(
        self, execution: Execution, *, approved: bool, decided_by: str, reason: str | None = None
    ) -> tuple[WorkflowState, Approval | None]:
        """Retoma um grafo pausado, com a decisao humana.

        Nada do que ja rodou e reexecutado: o LangGraph parte do checkpoint e retoma
        dentro do no que pausou. Na pratica, isso significa que uma aprovacao nao paga de
        novo os tokens da pesquisa, da analise e do planejamento.
        """
        graph = self._build(execution)

        bruto = await graph.ainvoke(
            Command(resume={"approved": approved, "decided_by": decided_by, "reason": reason}),
            config=self._config(execution.id),
        )

        estado, pendencia = self._split(bruto)
        logger.info(
            "workflow_resumed",
            execution_id=execution.id,
            approved=approved,
            decided_by=decided_by,
        )
        aprovacao = await self._finish(execution, estado, pendencia)

        if aprovacao is None:
            # Terminou de vez. E aqui -- e SO aqui -- que o callback faz sentido: quem
            # disparou a execucao recebeu `waiting_approval` como resposta e foi embora.
            # Em `run()` nao ha callback porque o resultado sai na propria resposta HTTP;
            # publicar la entregaria a mesma informacao duas vezes.
            await self._publish(execution, estado)

        return estado, aprovacao

    # ------------------------------------------------------------------ apoio

    async def _publish(self, execution: Execution, state: WorkflowState) -> None:
        """Entrega o resultado final a quem configurou o callback.

        O corpo publicado e **identico** ao que `POST /agents/run` devolveria. O consumidor
        (n8n, tipicamente) precisa entender um formato so, tenha a execucao terminado na
        resposta HTTP ou horas depois de uma aprovacao. E por isso que este servico usa um
        schema de resposta: nao e vazamento da camada de API, e a decisao de ter UMA
        representacao publica do resultado, seja qual for o transporte -- a mesma que o
        servidor MCP do V6 vai reaproveitar.
        """
        payload = AgentRunResponse.from_execution(execution, state).model_dump(mode="json")
        await self._publisher.publish(payload)

    def _build(
        self, execution: Execution
    ) -> CompiledStateGraph[WorkflowState, None, WorkflowState, WorkflowState]:
        nodes = WorkflowNodes(
            execution=execution,
            repository=self._repository,
            triage=TriageAgent(self._provider),
            research=ResearchAgent(self._provider),
            analysis=AnalysisAgent(self._provider),
            automation=AutomationAgent(self._provider, self._tools),
            reporter=ReporterAgent(self._provider),
            retriever=self._retriever,
            tools=self._tools,
            quality=self._quality,
        )
        return build_graph(nodes, self._checkpointer)

    @staticmethod
    def _config(execution_id: str) -> Any:
        """O `thread_id` amarra os checkpoints a esta execucao.

        E por ele -- e so por ele -- que uma aprovacao encontra o grafo pausado, mesmo
        que o processo que o pausou nao exista mais.
        """
        return {"configurable": {"thread_id": execution_id}}

    @staticmethod
    def _split(bruto: Any) -> tuple[WorkflowState, PendingAction | None]:
        """Separa o estado do canal de controle do LangGraph."""
        dados = dict(bruto)
        interrupcoes = dados.pop(INTERRUPT_KEY, None)

        # `ainvoke` devolve o estado como dicionario simples; o TypedDict e a nossa visao
        # tipada dele, nao uma classe em tempo de execucao.
        estado = cast(WorkflowState, dados)
        if not interrupcoes:
            return estado, None

        valor = getattr(interrupcoes[0], "value", {}) or {}
        return estado, PendingAction(
            tool=str(valor.get("tool", "")),
            arguments=dict(valor.get("arguments", {})),
            reason=valor.get("reason"),
        )

    async def _finish(
        self, execution: Execution, state: WorkflowState, pendencia: PendingAction | None
    ) -> Approval | None:
        if pendencia is not None:
            # A execucao NAO acabou: nada de `mark_finished`, que carimbaria duracao e
            # encerraria o registro. Ela esta viva, esperando uma pessoa.
            await self._repository.update_status(execution, ExecutionStatus.WAITING_APPROVAL)

            # Reaproveita a pendencia aberta em vez de criar outra. O grafo pode ser
            # reinvocado sem decisao nenhuma no meio, e duas linhas pendentes para a mesma
            # acao dariam a impressao de que ha duas coisas a decidir.
            aprovacao = await self._approvals.get_pending_for_execution(execution.id)
            if aprovacao is None:
                aprovacao = await self._approvals.create(
                    execution_id=execution.id,
                    tool=pendencia.tool,
                    arguments=pendencia.arguments,
                    reason=pendencia.reason,
                )

            await self._repository.load_steps(execution)
            logger.info(
                "workflow_waiting_approval",
                execution_id=execution.id,
                approval_id=aprovacao.id,
                tool=pendencia.tool,
            )
            return aprovacao

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
            avaliacao = state.get("quality") or {}
            reprovado = bool(avaliacao) and not avaliacao.get("passed", True)
            await self._repository.mark_finished(
                execution,
                # Reprovado no portao NAO vira `failed`: o trabalho foi feito, o resultado
                # esta ali, e o que se sabe e que ele nao passou na avaliacao. Marcar como
                # falha esconderia a resposta de quem poderia julga-la, e um estado
                # proprio e justamente o que permite alguem procurar por esses casos.
                status=(
                    ExecutionStatus.NEEDS_HUMAN_REVIEW if reprovado else ExecutionStatus.COMPLETED
                ),
                quality_score=avaliacao.get("score"),
                result={
                    "plan": state.get("plan"),
                    "research": state.get("research"),
                    "analysis": state.get("analysis"),
                    "automation": state.get("automation"),
                    "report": state.get("report"),
                    "quality": avaliacao or None,
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
            quality_score=execution.quality_score,
            cost_usd=execution.total_cost_usd,
            duration_ms=execution.duration_ms,
        )
        return None
