"""Politica de retry com backoff exponencial e jitter.

Escrita a mao, em vez de usar `tenacity`, por dois motivos: sao poucas linhas de logica
que precisamos poder explicar e testar deterministicamente, e o controle do jitter e do
predicado de retry fica explicito no codigo.

O jitter existe para evitar o "thundering herd": se dez execucoes falharem no mesmo
instante por um 429, um backoff fixo faria as dez tentarem de novo simultaneamente,
reproduzindo o mesmo pico que causou a falha.
"""

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class RetryPolicy:
    """Parametros de uma politica de retry."""

    max_attempts: int = 3
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 8.0
    jitter: bool = True

    def delay_for(self, attempt: int) -> float:
        """Espera antes da tentativa numero `attempt` (1 = primeira repeticao).

        Backoff exponencial limitado por `max_delay_seconds`. Com jitter, sorteia um
        valor no intervalo [0, delay] -- a estrategia "full jitter".
        """
        delay = min(self.base_delay_seconds * (2 ** (attempt - 1)), self.max_delay_seconds)
        return random.uniform(0, delay) if self.jitter else delay


async def retry_async[T](
    operation: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy,
    should_retry: Callable[[Exception], bool],
    operation_name: str = "operation",
) -> T:
    """Executa `operation`, repetindo enquanto a politica permitir.

    Args:
        operation: funcao sem argumentos que devolve um awaitable. Recriada a cada
            tentativa -- um coroutine object so pode ser aguardado uma vez.
        policy: quantas tentativas e qual o intervalo entre elas.
        should_retry: decide se a excecao merece nova tentativa.
        operation_name: rotulo usado nos logs.

    Raises:
        Exception: a excecao da ultima tentativa, quando todas falharem.
    """
    last_error: Exception | None = None

    for attempt in range(1, policy.max_attempts + 1):
        try:
            return await operation()
        except Exception as error:  # o predicado `should_retry` decide o que fazer
            last_error = error
            is_last_attempt = attempt == policy.max_attempts

            if not should_retry(error) or is_last_attempt:
                logger.warning(
                    "retry_exhausted",
                    operation=operation_name,
                    attempt=attempt,
                    max_attempts=policy.max_attempts,
                    error_type=type(error).__name__,
                    retryable=should_retry(error),
                )
                raise

            delay = policy.delay_for(attempt)
            logger.warning(
                "retry_scheduled",
                operation=operation_name,
                attempt=attempt,
                max_attempts=policy.max_attempts,
                error_type=type(error).__name__,
                delay_seconds=round(delay, 3),
            )
            await asyncio.sleep(delay)

    # Inalcancavel: o laco sempre retorna ou levanta. Presente para o verificador de tipos.
    raise last_error if last_error else RuntimeError("retry_async terminou sem resultado")
