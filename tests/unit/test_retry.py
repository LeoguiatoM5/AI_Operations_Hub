"""Testes da politica de retry.

Sem jitter e sem espera real: o objetivo e verificar a LOGICA de repeticao, nao
gastar segundos de relogio. O `monkeypatch` em `asyncio.sleep` mantem a suite rapida.
"""

import asyncio

import pytest

from app.core.retry import RetryPolicy, retry_async
from app.llm.exceptions import LLMAuthenticationError, LLMTimeoutError


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Substitui a espera real, guardando os intervalos solicitados."""
    delays: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return delays


def always_retry(error: Exception) -> bool:
    return True


def retry_only_transient(error: Exception) -> bool:
    return isinstance(error, LLMTimeoutError)


async def test_returns_result_without_retrying_on_success() -> None:
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    result = await retry_async(
        operation, policy=RetryPolicy(max_attempts=3), should_retry=always_retry
    )

    assert result == "ok"
    assert calls == 1


async def test_retries_until_success() -> None:
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise LLMTimeoutError()
        return "ok"

    result = await retry_async(
        operation, policy=RetryPolicy(max_attempts=3), should_retry=retry_only_transient
    )

    assert result == "ok"
    assert calls == 3


async def test_raises_after_exhausting_attempts() -> None:
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        raise LLMTimeoutError()

    with pytest.raises(LLMTimeoutError):
        await retry_async(
            operation, policy=RetryPolicy(max_attempts=3), should_retry=retry_only_transient
        )

    assert calls == 3


async def test_does_not_retry_permanent_failure() -> None:
    """Chave invalida nao melhora com insistencia: falhar rapido economiza tempo e cota."""
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        raise LLMAuthenticationError()

    with pytest.raises(LLMAuthenticationError):
        await retry_async(
            operation, policy=RetryPolicy(max_attempts=5), should_retry=retry_only_transient
        )

    assert calls == 1


def test_backoff_grows_exponentially_and_respects_ceiling() -> None:
    policy = RetryPolicy(base_delay_seconds=1.0, max_delay_seconds=4.0, jitter=False)

    assert policy.delay_for(1) == 1.0
    assert policy.delay_for(2) == 2.0
    assert policy.delay_for(3) == 4.0
    assert policy.delay_for(4) == 4.0  # limitado pelo teto


def test_jitter_keeps_delay_within_bounds() -> None:
    """Full jitter sorteia em [0, delay]: nunca negativo, nunca acima do teto."""
    policy = RetryPolicy(base_delay_seconds=1.0, max_delay_seconds=4.0, jitter=True)

    delays = [policy.delay_for(3) for _ in range(200)]

    assert all(0.0 <= delay <= 4.0 for delay in delays)
    assert len(set(delays)) > 1  # de fato aleatorio
