"""Acesso as execucoes.

O repositorio isola o SQL do resto da aplicacao. Sem essa fronteira, consultas comecam
a aparecer dentro de rotas e de agentes, e trocar SQLite por PostgreSQL -- ou testar um
agente sem banco -- vira uma caceteada em dezenas de arquivos.
"""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.types import utcnow
from app.models.enums import ExecutionStatus
from app.models.execution import AgentExecution, Execution


class ExecutionRepository:
    """Operacoes de leitura e escrita sobre execucoes e seus passos."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------ escrita

    async def create(
        self,
        *,
        request_text: str,
        correlation_id: str | None = None,
        status: ExecutionStatus = ExecutionStatus.PENDING,
    ) -> Execution:
        execution = Execution(
            request_text=request_text,
            correlation_id=correlation_id,
            status=status,
        )
        self._session.add(execution)
        await self._session.flush()  # atribui o id sem encerrar a transacao
        return execution

    async def add_agent_step(
        self,
        execution: Execution,
        *,
        agent: str,
        action: str,
        status: ExecutionStatus,
        input_data: dict[str, Any] | None = None,
        output_data: dict[str, Any] | None = None,
        provider: str | None = None,
        model: str | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cost_usd: float = 0.0,
        latency_ms: float | None = None,
        attempts: int = 1,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> AgentExecution:
        """Registra um passo e atualiza os agregados da execucao."""
        step = AgentExecution(
            execution_id=execution.id,
            sequence=await self._next_sequence(execution.id),
            agent=agent,
            action=action,
            status=status,
            input=input_data,
            output=output_data,
            provider=provider,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            attempts=attempts,
            error_code=error_code,
            error_message=error_message,
            finished_at=utcnow(),
        )
        # `session.add` em vez de `execution.agent_executions.append`: mexer na colecao
        # dispararia o carregamento dela, e carregamento implicito e proibido em
        # SQLAlchemy assincrono (MissingGreenlet). A chave estrangeira ja liga os dois.
        self._session.add(step)

        # Agregados atualizados aqui, e nao somados a cada leitura: listar cem execucoes
        # nao deve exigir varrer todos os passos de cada uma.
        execution.total_prompt_tokens += prompt_tokens
        execution.total_completion_tokens += completion_tokens
        execution.total_cost_usd = round(execution.total_cost_usd + cost_usd, 8)

        await self._session.flush()
        return step

    async def mark_finished(
        self,
        execution: Execution,
        *,
        status: ExecutionStatus,
        result: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        quality_score: float | None = None,
    ) -> Execution:
        """Encerra a execucao, calculando a duracao total."""
        finished_at = utcnow()
        execution.status = status
        execution.result = result
        execution.error_code = error_code
        execution.error_message = error_message
        execution.quality_score = quality_score
        execution.finished_at = finished_at
        execution.duration_ms = round(
            (finished_at - execution.created_at).total_seconds() * 1000, 3
        )
        await self._session.flush()
        return execution

    async def update_status(self, execution: Execution, status: ExecutionStatus) -> Execution:
        execution.status = status
        await self._session.flush()
        return execution

    async def commit(self) -> None:
        """Confirma a transacao atual.

        Vazamento deliberado do controle transacional para fora do repositorio, usado em
        um unico caso: gravar o registro de uma falha antes de propagar a excecao. Sem
        isso, o rollback disparado pelo erro apagaria justamente a auditoria da falha.
        """
        await self._session.commit()

    async def load_steps(self, execution: Execution) -> None:
        """Garante que a cadeia de passos esteja carregada no objeto.

        Necessario porque `add_agent_step` insere o passo sem tocar na colecao (para nao
        disparar I/O implicito). Quem for serializar a execucao precisa pedir o
        carregamento explicitamente.
        """
        await self._session.refresh(execution, attribute_names=["agent_executions"])

    async def _next_sequence(self, execution_id: str) -> int:
        """Proxima posicao livre na cadeia de passos.

        Consulta explicita, e nao `len(execution.agent_executions)`: ler uma relacao
        nao carregada dispara I/O implicito, que o SQLAlchemy assincrono recusa.
        """
        statement = select(func.coalesce(func.max(AgentExecution.sequence), 0)).where(
            AgentExecution.execution_id == execution_id
        )
        current_max = int((await self._session.execute(statement)).scalar_one())
        return current_max + 1

    # ------------------------------------------------------------------ leitura

    async def get(self, execution_id: str) -> Execution | None:
        """Busca uma execucao com seus passos ja carregados (`lazy="selectin"`)."""
        return await self._session.get(Execution, execution_id)

    async def list_steps(self, execution_id: str) -> Sequence[AgentExecution]:
        """Passos de uma execucao, em ordem."""
        statement = (
            select(AgentExecution)
            .where(AgentExecution.execution_id == execution_id)
            .order_by(AgentExecution.sequence)
        )
        result = await self._session.execute(statement)
        return result.scalars().all()

    async def list(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        status: ExecutionStatus | None = None,
    ) -> Sequence[Execution]:
        """Lista execucoes da mais recente para a mais antiga."""
        statement = select(Execution).order_by(Execution.created_at.desc())
        if status is not None:
            statement = statement.where(Execution.status == status)
        statement = statement.limit(limit).offset(offset)

        result = await self._session.execute(statement)
        return result.scalars().all()

    async def count(self, *, status: ExecutionStatus | None = None) -> int:
        statement = select(func.count()).select_from(Execution)
        if status is not None:
            statement = statement.where(Execution.status == status)
        return int((await self._session.execute(statement)).scalar_one())
