"""Testes do repositorio de execucoes."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import ExecutionStatus
from app.repositories.execution_repository import ExecutionRepository


async def test_creates_execution_with_generated_id(executions: ExecutionRepository) -> None:
    execution = await executions.create(
        request_text="Analise os chamados criticos de hoje.",
        correlation_id="req-123",
    )

    assert execution.id
    assert len(execution.id) == 32
    assert execution.status == ExecutionStatus.PENDING
    assert execution.correlation_id == "req-123"
    assert execution.total_cost_usd == 0.0


async def test_id_is_available_before_commit(executions: ExecutionRepository) -> None:
    """O id precisa existir para ser logado e devolvido enquanto a transacao ainda corre."""
    execution = await executions.create(request_text="pedido")

    assert execution.id is not None


async def test_persists_across_sessions(
    executions: ExecutionRepository, session: AsyncSession
) -> None:
    execution = await executions.create(request_text="pedido")
    execution_id = execution.id
    await session.commit()
    session.expunge_all()  # esvazia o cache de identidade: forca ida ao banco

    recovered = await executions.get(execution_id)

    assert recovered is not None
    assert recovered.request_text == "pedido"


async def test_unknown_id_returns_none(executions: ExecutionRepository) -> None:
    assert await executions.get("nao-existe") is None


async def test_agent_steps_are_numbered_in_order(executions: ExecutionRepository) -> None:
    execution = await executions.create(request_text="pedido")

    for agent in ("research", "analysis", "reporter"):
        await executions.add_agent_step(
            execution, agent=agent, action="run", status=ExecutionStatus.COMPLETED
        )

    steps = await executions.list_steps(execution.id)

    assert [step.sequence for step in steps] == [1, 2, 3]
    assert [step.agent for step in steps] == ["research", "analysis", "reporter"]


async def test_steps_are_eagerly_loaded_when_reading_an_execution(
    executions: ExecutionRepository, session: AsyncSession
) -> None:
    """`lazy="selectin"` traz a cadeia junto: ler a relacao depois nao dispara I/O oculto.

    Sem isso, `execution.agent_executions` levantaria MissingGreenlet no codigo assincrono.
    """
    execution = await executions.create(request_text="pedido")
    await executions.add_agent_step(
        execution, agent="research", action="run", status=ExecutionStatus.COMPLETED
    )
    await session.commit()
    session.expunge_all()

    recovered = await executions.get(execution.id)

    assert recovered is not None
    assert [step.agent for step in recovered.agent_executions] == ["research"]


async def test_aggregates_tokens_and_cost_from_steps(executions: ExecutionRepository) -> None:
    """Somar na escrita evita varrer todos os passos a cada listagem."""
    execution = await executions.create(request_text="pedido")

    await executions.add_agent_step(
        execution,
        agent="research",
        action="rag_query",
        status=ExecutionStatus.COMPLETED,
        prompt_tokens=100,
        completion_tokens=50,
        cost_usd=0.000045,
    )
    await executions.add_agent_step(
        execution,
        agent="reporter",
        action="write_report",
        status=ExecutionStatus.COMPLETED,
        prompt_tokens=200,
        completion_tokens=300,
        cost_usd=0.00021,
    )

    assert execution.total_prompt_tokens == 300
    assert execution.total_completion_tokens == 350
    assert execution.total_tokens == 650
    assert execution.total_cost_usd == pytest.approx(0.000255)


async def test_step_stores_provider_metadata(executions: ExecutionRepository) -> None:
    execution = await executions.create(request_text="pedido")

    step = await executions.add_agent_step(
        execution,
        agent="research",
        action="rag_query",
        status=ExecutionStatus.COMPLETED,
        input_data={"query": "chamados criticos"},
        output_data={"chunks": 4},
        provider="openai",
        model="gpt-4o-mini-2024-07-18",
        latency_ms=1234.5,
        attempts=2,
    )

    assert step.input == {"query": "chamados criticos"}
    assert step.output == {"chunks": 4}
    assert step.provider == "openai"
    assert step.attempts == 2


async def test_failed_step_records_the_error(executions: ExecutionRepository) -> None:
    execution = await executions.create(request_text="pedido")

    step = await executions.add_agent_step(
        execution,
        agent="automation",
        action="call_webhook",
        status=ExecutionStatus.FAILED,
        error_code="llm_timeout",
        error_message="O provedor de LLM nao respondeu a tempo.",
    )

    assert step.status == ExecutionStatus.FAILED
    assert step.error_code == "llm_timeout"


async def test_mark_finished_computes_duration(executions: ExecutionRepository) -> None:
    execution = await executions.create(request_text="pedido")

    await executions.mark_finished(
        execution,
        status=ExecutionStatus.COMPLETED,
        result={"summary": "3 problemas recorrentes"},
        quality_score=92.0,
    )

    assert execution.status == ExecutionStatus.COMPLETED
    assert execution.result == {"summary": "3 problemas recorrentes"}
    assert execution.quality_score == 92.0
    assert execution.duration_ms is not None
    assert execution.duration_ms >= 0
    assert execution.finished_at is not None


async def test_timestamps_are_timezone_aware_utc(
    executions: ExecutionRepository, session: AsyncSession
) -> None:
    """Sem isso, subtrair datas lidas do banco levanta TypeError ou mente na duracao."""
    execution = await executions.create(request_text="pedido")
    await session.commit()
    session.expunge_all()

    recovered = await executions.get(execution.id)

    assert recovered is not None
    assert recovered.created_at.tzinfo is not None
    assert recovered.created_at.utcoffset() == UTC.utcoffset(None)
    # A prova pratica: comparar com "agora" nao pode explodir.
    assert (datetime.now(UTC) - recovered.created_at).total_seconds() >= 0


async def test_lists_most_recent_first(executions: ExecutionRepository) -> None:
    for index in range(3):
        await executions.create(request_text=f"pedido {index}")

    listed = await executions.list()

    assert len(listed) == 3
    assert [item.created_at for item in listed] == sorted(
        [item.created_at for item in listed], reverse=True
    )


async def test_list_filters_by_status(executions: ExecutionRepository) -> None:
    pending = await executions.create(request_text="a")
    done = await executions.create(request_text="b")
    await executions.mark_finished(done, status=ExecutionStatus.COMPLETED)

    completed = await executions.list(status=ExecutionStatus.COMPLETED)

    assert [item.id for item in completed] == [done.id]
    assert pending.id not in {item.id for item in completed}


async def test_list_paginates(executions: ExecutionRepository) -> None:
    for index in range(5):
        await executions.create(request_text=f"pedido {index}")

    first_page = await executions.list(limit=2, offset=0)
    second_page = await executions.list(limit=2, offset=2)

    assert len(first_page) == 2
    assert len(second_page) == 2
    assert {item.id for item in first_page}.isdisjoint({item.id for item in second_page})


async def test_counts_by_status(executions: ExecutionRepository) -> None:
    await executions.create(request_text="a")
    done = await executions.create(request_text="b")
    await executions.mark_finished(done, status=ExecutionStatus.COMPLETED)

    assert await executions.count() == 2
    assert await executions.count(status=ExecutionStatus.COMPLETED) == 1


async def test_deleting_execution_removes_its_steps(
    executions: ExecutionRepository, session: AsyncSession
) -> None:
    """Cascata evita passos orfaos apontando para uma execucao que nao existe mais."""
    execution = await executions.create(request_text="pedido")
    await executions.add_agent_step(
        execution, agent="research", action="run", status=ExecutionStatus.COMPLETED
    )
    await session.commit()

    await session.delete(execution)
    await session.commit()

    from sqlalchemy import func, select

    from app.models.execution import AgentExecution

    remaining = await session.execute(select(func.count()).select_from(AgentExecution))
    assert remaining.scalar_one() == 0
