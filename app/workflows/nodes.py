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

from langgraph.types import interrupt

from app.agents.analysis import AnalysisAgent
from app.agents.automation import AutomationAgent
from app.agents.base import AgentOutcome
from app.agents.reporter import ReporterAgent
from app.agents.research import ResearchAgent
from app.agents.triage import TriageAgent
from app.core.exceptions import AIHubError
from app.core.logging import get_logger
from app.models.enums import ExecutionStatus
from app.models.execution import Execution
from app.quality.base import StepFacts
from app.quality.engine import QualityEngine
from app.quality.subject import subject_from_report
from app.rag.retriever import Retriever
from app.repositories.execution_repository import ExecutionRepository
from app.tools.registry import ToolRegistry
from app.workflows.state import WorkflowState, split_plan

logger = get_logger(__name__)


def _foi_aprovada(decisao: Any) -> bool:
    """Interpreta o valor devolvido pela retomada do grafo.

    Estrito de proposito: so um `approved` explicitamente verdadeiro autoriza. Qualquer
    outra coisa -- `None`, dicionario vazio, formato inesperado -- conta como NAO
    aprovado. Numa acao irreversivel, a ambiguidade tem que cair para o lado seguro.
    """
    return isinstance(decisao, dict) and decisao.get("approved") is True


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
        automation: AutomationAgent,
        reporter: ReporterAgent,
        retriever: Retriever,
        tools: ToolRegistry,
        quality: QualityEngine | None = None,
    ) -> None:
        self._execution = execution
        self._repository = repository
        self._triage = triage
        self._research = research
        self._analysis = analysis
        self._automation = automation
        self._reporter = reporter
        self._retriever = retriever
        self._tools = tools
        self._quality = quality

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
        pendentes, ignorados = split_plan(list(plano.suggested_agents))

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

    # ------------------------------------------------------------------ automacao

    async def automation_plan(self, state: WorkflowState) -> dict[str, Any]:
        """Escolhe a ferramenta e monta os argumentos. NAO executa nada.

        A separacao entre decidir e executar existe por causa da pausa: a retomada de um
        `interrupt()` reexecuta o no inteiro, e nao a linha seguinte. Se a escolha
        estivesse aqui dentro do no que pausa, cada aprovacao pagaria a chamada de LLM de
        novo e poderia produzir uma acao diferente da que o humano viu.
        """
        restante = self._without("automation", state)
        material = {
            "dados_de_entrada": state.get("input_data"),
            "pesquisa": state.get("research"),
            "analise": state.get("analysis"),
        }

        try:
            outcome = await self._automation.run(state["request_text"], material)
        except AIHubError as error:
            await self._record_failure("automation", "choose_tool", error)
            return {
                "tool_call": None,
                "errors": [{"agent": "automation", "code": error.code, "message": error.message}],
                "pending_agents": restante,
            }

        await self._record(outcome, agent="automation", action=outcome.action)
        chamada = outcome.payload

        return {
            "tool_call": {
                **chamada.model_dump(),
                # Resolvido aqui, na decisao, e nao na execucao: assim a exigencia de
                # aprovacao fica gravada no checkpoint junto com a acao. Se o catalogo
                # mudar entre a pausa e a retomada, vale o que estava valendo quando a
                # pessoa foi consultada.
                "requires_approval": self._tools.requires_approval(chamada.tool),
            },
            "pending_agents": restante,
        }

    async def automation_run(self, state: WorkflowState) -> dict[str, Any]:
        """Executa a acao -- pausando antes, se ela alterar sistema externo.

        `interrupt()` e a PRIMEIRA coisa que acontece quando ha aprovacao a pedir. Tudo
        que estivesse acima dele rodaria duas vezes: uma na ida, outra na retomada.
        """
        chamada = state.get("tool_call")
        if not chamada:
            # O planejamento falhou e ja registrou o motivo. Nao ha acao a executar.
            return {"automation": None}

        if chamada.get("requires_approval"):
            decisao = interrupt(
                {
                    "type": "tool_approval",
                    "execution_id": state["execution_id"],
                    "tool": chamada["tool"],
                    "arguments": chamada["arguments"],
                    "reason": chamada.get("reason"),
                }
            )
            if not _foi_aprovada(decisao):
                return await self._record_rejection(chamada, decisao)

        try:
            resultado = await self._tools.execute(chamada["tool"], chamada["arguments"])
        except AIHubError as error:
            await self._record_failure("automation", "execute_tool", error)
            return {
                "automation": None,
                "errors": [{"agent": "automation", "code": error.code, "message": error.message}],
            }

        await self._repository.add_agent_step(
            self._execution,
            agent="automation",
            action="execute_tool",
            status=ExecutionStatus.COMPLETED,
            input_data={"tool": chamada["tool"], "arguments": chamada["arguments"]},
            output_data=resultado.model_dump(),
            latency_ms=resultado.latency_ms,
        )
        logger.info(
            "automation_executed",
            execution_id=state["execution_id"],
            tool=chamada["tool"],
            required_approval=bool(chamada.get("requires_approval")),
        )

        return {
            "automation": {
                "executed": True,
                "tool": chamada["tool"],
                "summary": resultado.summary,
                "output": resultado.output,
            },
            "completed": ["automation"],
        }

    async def _record_rejection(self, chamada: dict[str, Any], decisao: Any) -> dict[str, Any]:
        """Registra uma acao recusada por um humano.

        Recusa NAO e falha: o sistema funcionou exatamente como deveria. Por isso o passo
        entra como `completed`, com o desfecho no `output` -- gravar como `failed` faria
        um painel de erros acusar problema toda vez que alguem dissesse "nao".
        """
        motivo = decisao.get("reason") if isinstance(decisao, dict) else None
        quem = decisao.get("decided_by") if isinstance(decisao, dict) else None

        await self._repository.add_agent_step(
            self._execution,
            agent="automation",
            action="execute_tool",
            status=ExecutionStatus.COMPLETED,
            input_data={"tool": chamada["tool"], "arguments": chamada["arguments"]},
            output_data={"executed": False, "rejected": True, "decided_by": quem, "reason": motivo},
        )
        logger.info("automation_rejected", tool=chamada["tool"], decided_by=quem)

        return {
            "automation": {
                "executed": False,
                "rejected": True,
                "tool": chamada["tool"],
                "decided_by": quem,
                "reason": motivo,
            },
            "completed": ["automation"],
        }

    # ------------------------------------------------------------------ relatorio

    async def reporter(self, state: WorkflowState) -> dict[str, Any]:
        """Consolida tudo em um relatorio, inclusive o que falhou.

        Roda mais de uma vez quando o portao de qualidade reprova: a segunda passagem
        recebe o motivo exato da reprovacao no material. E o mesmo retry dirigido do
        ED-023, um nivel acima -- la o modelo recebia o erro de validacao de schema, aqui
        recebe o que a avaliacao considerou mal fundamentado ou incompleto.
        """
        material: dict[str, Any] = {
            "solicitacao": state["request_text"],
            "plano": state.get("plan"),
            "pesquisa": state.get("research"),
            "analise": state.get("analysis"),
            "automacao": state.get("automation"),
            "agentes_com_falha": state.get("errors", []),
            "agentes_nao_executados": state.get("skipped_agents", []),
        }

        feedback = state.get("quality_feedback", "")
        if feedback:
            material["reprovacao_da_versao_anterior"] = (
                "A versao anterior deste relatorio foi reprovada na avaliacao de "
                f"qualidade pelos motivos abaixo. Corrija-os:\n{feedback}"
            )

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

    # ------------------------------------------------------------------ qualidade

    async def quality(self, state: WorkflowState) -> dict[str, Any]:
        """Avalia o relatorio antes de entrega-lo.

        Sem motor configurado, o no e um passa-nada: devolve o estado intocado, sem
        gravar passo nem gastar token. E o caminho padrao do projeto -- o portao so liga
        quando alguem pede, porque ele custa quatro chamadas de LLM por execucao.
        """
        if self._quality is None:
            return {}

        tentativa = state.get("quality_attempts", 0) + 1
        passos = await self._repository.list_steps(self._execution.id)

        subject = subject_from_report(
            task=state["request_text"],
            report=state.get("report"),
            research=state.get("research"),
            steps=[
                StepFacts(
                    agent=passo.agent,
                    action=passo.action,
                    succeeded=passo.status == ExecutionStatus.COMPLETED,
                    attempts=passo.attempts,
                    error_code=passo.error_code,
                )
                for passo in passos
                # O proprio portao nao entra na conta de confiabilidade: medir a saude da
                # execucao incluindo o medidor seria contar a si mesmo.
                if passo.agent != "quality"
            ],
        )

        report = await self._quality.evaluate(subject)

        await self._repository.add_agent_step(
            self._execution,
            agent="quality",
            action="evaluate",
            status=ExecutionStatus.COMPLETED,
            output_data=report.model_dump(mode="json"),
            cost_usd=report.cost_usd,
            attempts=tentativa,
        )
        logger.info(
            "quality_gate",
            execution_id=state["execution_id"],
            attempt=tentativa,
            score=report.score,
            passed=report.passed,
            cost_usd=report.cost_usd,
        )

        return {
            "quality": report.model_dump(mode="json"),
            "quality_attempts": tentativa,
            # O feedback so faz sentido se houver outra tentativa; quando nao houver, o
            # roteador ignora. Escrever aqui mantem o no puro em relacao a decisao.
            "quality_feedback": report.feedback(),
        }

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
