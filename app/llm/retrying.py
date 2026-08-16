"""Decorador de retry sobre qualquer provider.

Padrao Decorator: `RetryingLLMProvider` satisfaz o mesmo Protocol que decora, entao
quem o consome nao sabe (nem precisa saber) que ha uma politica de repeticao no meio.

Manter o retry fora dos providers evita reimplementa-lo em OpenAI, Anthropic e Gemini,
e permite testar a politica isoladamente com o provider falso.
"""

from collections.abc import Sequence

from app.core.logging import get_logger
from app.core.retry import RetryPolicy, retry_async
from app.llm.base import LLMMessage, LLMProvider, LLMResponse
from app.llm.exceptions import LLMError

logger = get_logger(__name__)


def _is_retryable(error: Exception) -> bool:
    """So repete o que tem chance de dar certo numa segunda tentativa.

    Timeout e limite de cota sao transitorios; credencial invalida nao melhora com
    insistencia -- repetir apenas atrasaria o erro e gastaria cota.
    """
    return isinstance(error, LLMError) and error.retryable


class RetryingLLMProvider:
    """Envolve um provider aplicando backoff exponencial a falhas transitorias."""

    def __init__(self, inner: LLMProvider, policy: RetryPolicy) -> None:
        self._inner = inner
        self._policy = policy

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def model(self) -> str:
        return self._inner.model

    async def complete(
        self,
        messages: Sequence[LLMMessage],
        *,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        json_mode: bool = False,
    ) -> LLMResponse:
        attempts = 0

        async def call() -> LLMResponse:
            nonlocal attempts
            attempts += 1
            return await self._inner.complete(
                messages,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                json_mode=json_mode,
            )

        response = await retry_async(
            call,
            policy=self._policy,
            should_retry=_is_retryable,
            operation_name=f"llm.complete[{self._inner.name}]",
        )
        # Quantas tentativas foram necessarias e dado de observabilidade: uma execucao
        # que so funcionou na terceira tentativa nao e igual a uma que funcionou de primeira.
        return response.model_copy(update={"attempts": attempts})

    async def aclose(self) -> None:
        await self._inner.aclose()
