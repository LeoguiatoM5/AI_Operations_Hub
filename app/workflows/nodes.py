"""Nos do grafo de agentes.

Duas regras valem para todos os nos:

**Nenhum no levanta excecao de LLM.** Uma falha vira um registro em `errors` e o grafo
continua com o que conseguiu. Isso e degradacao graciosa: um relatorio que diz "a
pesquisa falhou" vale mais que erro 502 sem nada aproveitado. A unica excecao e o
orquestrador -- sem plano, nao ha o que executar.

**Todo no grava seu proprio passo.** O rastro em `agent_executions` e escrito durante a
execucao, nao no final: se o processo morrer no meio, o que ja rodou continua visivel.
"""

from typing import Any

from app.agents.analysis import AnalysisAgent
from app.agents.base import AgentOutcome
from app.agents.reporter import ReporterAgent
from app.agents.research import ResearchAgent
from app.agents.triage import TriageAgent
from app.core.exceptions import AIHubError
from app.core.logging import get_logger
from app.models.enums import ExecutionStatus
from app.models.execution import Execution
from app.rag.retriever import Retriever
from app.repositories.execution_repository import ExecutionRepository
from app.workflows.state import EXECUTABLE_AGENTS, WorkflowState

logger = get_logger(__name__)


class WorkflowNodes:
    """Nos do grafo, com as dependencias de uma execucao.

    Construido por requisicao: a sessao de banco e a execucao pertencem ao request em
    curso, e nao ao processo.
    """

    def __init__(
        self,
        *,
        execution: Execution,
        repository: ExecutionRepository,
        triage: TriageAgent,
        research: ResearchAgent,
        analysis: AnalysisAgent,
        reporter: ReporterAgent,
        retriever: Retriever,
    ) -> None:
        self._execution = execution
        self._repository = repository
        self._triage = triage
        self._research = research
        self._analysis = analysis
        self._reporter = reporter
        self._retriever = retriever

    # ------------------------------------------------------------------ orquestrador

    async def orchestrator(self, state: WorkflowState) -> dict[str, Any]:
        """Interpreta a solicitacao e monta a fila de agentes."""
        try:
            outcome = await self._triage.run(state["request_text"])
        except AIHubError as error:
            await self._record_failure("orchestrator", "plan", error)
            # Sem plano nao ha caminho a seguir: esta e a unica falha fatal do grafo.
            return {
                "errors": [{"agent": "orchestrator", "code": error.code, "message": error.message}],
                "fatal": True,
            }

        plano = outcome.payload
        sugeridos = list(plano.suggested_agents)
        pendentes = [nome for nome in sugeridos if nome in EXECUTABLE_AGENTS]
        ignorados = [
            nome for nome in sugeridos if nome not in EXECUTABLE_AGENTS and nome != "reporter"
        ]

        await self._record(outcome, agent="orchestrator", action="plan")

        logger.info(
            "workflow_planned",
            execution_id=state["execution_id"],
            intent=plano.intent,
            pending=pendentes,
            skipped=ignorados,
            requires_approval=plano.requires_approval,
        )

        return {
            "plan": plano.model_dump(),
            "pending_agents": pendentes,
            "skipped_agents": ignorados,
            "completed": ["orchestrator"],
        }

    # ------------------------------------------------------------------ pesquisa

    async def research(self, state: WorkflowState) -> dict[str, Any]:
        """Consulta a base de conhecimento."""
        restante = self._without("research", state)
        try:
            recuperado = await self._retriever.retrieve(state["request_text"])
            if not recuperado.has_context:
                # Base sem cobertura nao e erro: e informacao para o relatorio.
                await self._record_skip(
                    "research",
                    "answer_from_knowledge_base",
                    "A base de conhecimento nao cobre esta solicitacao.",
                )
                return {
                    "research": {
                        "answered": False,
                        "reason": "sem contexto relevante na base de conhecimento",
                        "chunks_discarded": len(recuperado.discarded),
                    },
                    "pending_agents": restante,
                    "completed": ["research"],
                }

            outcome = await self._research.run(state["request_text"], recuperado.hits)
        except AIHubError as error:
            await self._record_failure("research", "answer_from_knowledge_base", error)
            return {
                "research": None,
                "errors": [{"agent": "research", "code": error.code, "message": error.message}],
                "pending_agents": restante,
            }

        await self._record(outcome, agent="research", action=outcome.action)
        citadas = [recuperado.hits[numero - 1] for numero in outcome.payload.citations]

        return {
            "research": {
                **outcome.payload.model_dump(),
                "sources": [
                    {
                        "document_id": hit.document_id,
                        "filename": hit.metadata.get("filename"),
                        "score": round(hit.score, 4),
                        "excerpt": hit.text[:300],
                    }
                    for hit in citadas
                ],
            },
            "pending_agents": restante,
            "completed": ["research"],
        }

    # ------------------------------------------------------------------ analise

    async def analysis(self, state: WorkflowState) -> dict[str, Any]:
        """Encontra padroes nos dados recebidos ou no que a pesquisa produziu."""
        restante = self._without("analysis", state)
        material = state.get("input_data") or state.get("research")
        if not material:
            await self._record_skip(
                "analysis", "find_patterns", "Nenhum dado foi fornecido para analise."
            )
            return {
                "analysis": {"skipped": True, "reason": "nenhum dado disponivel para analisar"},
                "pending_agents": restante,
                "completed": ["analysis"],
            }

        try:
            outcome = await self._analysis.run(state["request_text"], material)
        except AIHubError as error:
            await self._record_failure("analysis", "find_patterns", error)
            return {
                "analysis": None,
                "errors": [{"agent": "analysis", "code": error.code, "message": error.message}],
                "pending_agents": restante,
            }

        await self._record(outcome, agent="analysis", action=outcome.action)
        return {
            "analysis": outcome.payload.model_dump(),
            "pending_agents": restante,
            "completed": ["analysis"],
        }

    # ------------------------------------------------------------------ relatorio

    async def reporter(self, state: WorkflowState) -> dict[str, Any]:
        """Consolida tudo em um relatorio, inclusive o que falhou."""
        material = {
            "solicitacao": state["request_text"],
            "plano": state.get("plan"),
            "pesquisa": state.get("research"),
            "analise": state.get("analysis"),
            "agentes_com_falha": state.get("errors", []),
            "agentes_nao_executados": state.get("skipped_agents", []),
        }

        try:
            outcome = await self._reporter.run(state["request_text"], material)
        except AIHubError as error:
            await self._record_failure("reporter", "write_report", error)
            return {
                "report": None,
                "errors": [{"agent": "reporter", "code": error.code, "message": error.message}],
            }

        await self._record(outcome, agent="reporter", action=outcome.action)
        return {"report": outcome.payload.model_dump(), "completed": ["reporter"]}

    # ------------------------------------------------------------------ apoio

    @staticmethod
    def _without(agent: str, state: WorkflowState) -> list[str]:
        """Remove o agente da fila. E assim que o grafo avanca sem laco infinito."""
        return [nome for nome in state.get("pending_agents", []) if nome != agent]

    async def _record(self, outcome: AgentOutcome[Any], *, agent: str, action: str) -> None:
        await self._repository.add_agent_step(
            self._execution,
            agent=agent,
            action=action,
            status=ExecutionStatus.COMPLETED,
            output_data=outcome.payload.model_dump(),
            provider=outcome.response.provider,
            model=outcome.response.model,
            prompt_tokens=outcome.response.usage.prompt_tokens,
            completion_tokens=outcome.response.usage.completion_tokens,
            cost_usd=outcome.response.cost_usd,
            latency_ms=outcome.response.latency_ms,
            attempts=outcome.response.attempts,
        )

    async def _record_failure(self, agent: str, action: str, error: AIHubError) -> None:
        await self._repository.add_agent_step(
            self._execution,
            agent=agent,
            action=action,
            status=ExecutionStatus.FAILED,
            error_code=error.code,
            error_message=error.message,
        )
        logger.warning(
            "workflow_node_failed",
            execution_id=self._execution.id,
            agent=agent,
            error_code=error.code,
        )

    async def _record_skip(self, agent: str, action: str, reason: str) -> None:
        """Registra um agente que rodou mas nao tinha o que fazer.

        Diferente de falha: nada quebrou. Mas precisa aparecer no rastro, senao a
        ausencia do agente no relatorio fica inexplicavel.
        """
        await self._repository.add_agent_step(
            self._execution,
            agent=agent,
            action=action,
            status=ExecutionStatus.COMPLETED,
            output_data={"skipped": True, "reason": reason},
        )
