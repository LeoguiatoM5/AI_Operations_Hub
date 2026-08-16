"""Provider da OpenAI.

Responsabilidade unica: traduzir entre o contrato do projeto e o SDK da OpenAI --
mensagens, parametros, excecoes e uso de tokens. Nenhuma regra de negocio aqui.
"""

from collections.abc import Sequence
from time import perf_counter
from typing import Any

import openai
from openai import AsyncOpenAI

from app.core.logging import get_logger
from app.llm.base import LLMMessage, LLMResponse, TokenUsage
from app.llm.exceptions import (
    LLMAuthenticationError,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from app.llm.pricing import estimate_cost_usd

logger = get_logger(__name__)


class OpenAIProvider:
    """Implementacao do Protocol LLMProvider sobre a API da OpenAI."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        temperature: float = 0.0,
        max_output_tokens: int = 1024,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._model = model
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens
        self._client = AsyncOpenAI(
            api_key=api_key,
            timeout=timeout_seconds,
            # O SDK tem retry proprio e silencioso. Desligado de proposito: o retry e
            # nosso (app/core/retry.py), para que tentativas e latencia sejam medidas
            # e registradas, em vez de escondidas dentro da biblioteca.
            max_retries=0,
        )

    @property
    def name(self) -> str:
        return "openai"

    @property
    def model(self) -> str:
        return self._model

    async def complete(
        self,
        messages: Sequence[LLMMessage],
        *,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        json_mode: bool = False,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": self._temperature if temperature is None else temperature,
            "max_tokens": max_output_tokens or self._max_output_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        started_at = perf_counter()
        try:
            completion = await self._client.chat.completions.create(**payload)
        except openai.APITimeoutError as error:
            raise LLMTimeoutError(details={"model": self._model}) from error
        except openai.RateLimitError as error:
            raise LLMRateLimitError(details={"model": self._model}) from error
        except openai.AuthenticationError as error:
            raise LLMAuthenticationError(details={"model": self._model}) from error
        except openai.APIStatusError as error:
            raise LLMError(
                f"Provedor respondeu com status {error.status_code}.",
                details={"model": self._model, "status_code": error.status_code},
            ) from error
        except openai.APIConnectionError as error:
            raise LLMError(
                "Nao foi possivel conectar ao provedor de LLM.",
                details={"model": self._model},
            ) from error

        latency_ms = round((perf_counter() - started_at) * 1000, 3)

        choice = completion.choices[0] if completion.choices else None
        content = (choice.message.content if choice and choice.message else None) or ""

        raw_usage = completion.usage
        usage = TokenUsage(
            prompt_tokens=raw_usage.prompt_tokens if raw_usage else 0,
            completion_tokens=raw_usage.completion_tokens if raw_usage else 0,
        )

        return LLMResponse(
            content=content,
            provider=self.name,
            model=completion.model or self._model,
            usage=usage,
            cost_usd=estimate_cost_usd(completion.model or self._model, usage),
            latency_ms=latency_ms,
            finish_reason=choice.finish_reason if choice else None,
        )

    async def aclose(self) -> None:
        await self._client.close()
