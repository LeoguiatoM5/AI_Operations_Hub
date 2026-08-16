"""Orquestracao de uma execucao.

Esta camada nao conhece FastAPI. E deliberado: o servidor MCP (V6) vai reaproveita-la
inteira, e uma dependencia de framework web aqui vazaria para um contexto onde nao faz
sentido nenhum. REST e MCP sao adaptadores; a regra de negocio mora aqui.
"""

from typing import Any

from app.agents.triage import TriageAgent
from app.core.logging import get_logger
from app.llm.exceptions import LLMError
from app.models.enums import ExecutionStatus
from app.models.execution import Execution
from app.repositories.execution_repository import ExecutionRepository

logger = get_logger(__name__)


class ExecutionService:
    """Executa uma solicitacao e registra tudo o que aconteceu no caminho."""

    def __init__(self, repository: ExecutionRepository, agent: TriageAgent) -> None:
        self._repository = repository
        self._agent = agent

    async def run(self, *, request_text: str, correlation_id: str | None = None) -> Execution:
        """Processa uma solicitacao do inicio ao fim.

        A execucao e gravada antes de qualquer chamada de LLM: se o processo morrer no
        meio, o registro do que foi pedido sobrevive.

        Raises:
            LLMError: falha do provedor. A execucao ja esta gravada como FAILED e o
                `execution_id` acompanha o erro, para que o rastro continue acessivel.
        """
        execution = await self._repository.create(
            request_text=request_text,
            correlation_id=correlation_id,
            status=ExecutionStatus.RUNNING,
        )
        logger.info("execution_started", execution_id=execution.id)

        try:
            outcome = await self._agent.run(request_text)
        except LLMError as error:
            await self._record_failure(execution, error)
            error.details["execution_id"] = execution.id
            raise

        payload: dict[str, Any] = outcome.payload.model_dump()
        await self._repository.add_agent_step(
            execution,
            agent=outcome.agent,
            action=outcome.action,
            status=ExecutionStatus.COMPLETED,
            input_data={"request_text": request_text},
            output_data=payload,
            provider=outcome.response.provider,
            model=outcome.response.model,
            prompt_tokens=outcome.response.usage.prompt_tokens,
            completion_tokens=outcome.response.usage.completion_tokens,
            cost_usd=outcome.response.cost_usd,
            latency_ms=outcome.response.latency_ms,
            attempts=outcome.response.attempts,
        )

        await self._repository.mark_finished(
            execution, status=ExecutionStatus.COMPLETED, result=payload
        )
        # Carrega a cadeia antes de devolver: quem serializar o objeto nao pode
        # disparar I/O implicito ao ler a relacao.
        await self._repository.load_steps(execution)
        logger.info(
            "execution_completed",
            execution_id=execution.id,
            duration_ms=execution.duration_ms,
            cost_usd=execution.total_cost_usd,
            repairs=outcome.repairs,
        )
        return execution

    async def _record_failure(self, execution: Execution, error: LLMError) -> None:
        """Registra o passo que falhou e encerra a execucao como FAILED."""
        await self._repository.add_agent_step(
            execution,
            agent=self._agent.name,
            action=self._agent.action,
            status=ExecutionStatus.FAILED,
            input_data={"request_text": execution.request_text},
            error_code=error.code,
            error_message=error.message,
        )
        await self._repository.mark_finished(
            execution,
            status=ExecutionStatus.FAILED,
            error_code=error.code,
            error_message=error.message,
        )
        # Confirmado aqui, e nao ao final do request: a excecao que estamos prestes a
        # propagar dispara rollback na sessao, e o registro da falha -- a auditoria que
        # torna o problema investigavel -- seria apagado junto.
        await self._repository.commit()
        logger.warning(
            "execution_failed",
            execution_id=execution.id,
            error_code=error.code,
        )
