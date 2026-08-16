"""Testes de contrato do provider da OpenAI.

Nao chamam a API real: substituimos o metodo do SDK e verificamos os dois lados da
traducao -- as excecoes do SDK viram a hierarquia do projeto, e a resposta bruta vira
um LLMResponse com uso, custo e latencia preenchidos.

Esta e a camada que quebra silenciosamente quando o SDK muda de versao, entao ela
merece teste explicito.
"""

from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest

from app.llm.base import LLMMessage
from app.llm.exceptions import (
    LLMAuthenticationError,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from app.llm.openai_provider import OpenAIProvider

MESSAGES = [LLMMessage.system("sistema"), LLMMessage.user("pergunta")]

_REQUEST = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


def _response(status_code: int) -> httpx.Response:
    return httpx.Response(status_code=status_code, request=_REQUEST)


def make_provider() -> OpenAIProvider:
    return OpenAIProvider(api_key="sk-teste", model="gpt-4o-mini")


def patch_create(
    monkeypatch: pytest.MonkeyPatch, provider: OpenAIProvider, outcome: Any
) -> list[dict[str, Any]]:
    """Substitui a chamada do SDK, devolvendo a lista de payloads enviados."""
    sent: list[dict[str, Any]] = []

    async def fake_create(**kwargs: Any) -> Any:
        sent.append(kwargs)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(provider._client.chat.completions, "create", fake_create)
    return sent


def completion(
    *, content: str = "resposta", prompt_tokens: int = 100, completion_tokens: int = 20
) -> Any:
    return SimpleNamespace(
        model="gpt-4o-mini",
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        ),
    )


async def test_translates_a_successful_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = make_provider()
    patch_create(monkeypatch, provider, completion())

    response = await provider.complete(MESSAGES)

    assert response.content == "resposta"
    assert response.provider == "openai"
    assert response.model == "gpt-4o-mini"
    assert response.usage.prompt_tokens == 100
    assert response.usage.total_tokens == 120
    assert response.cost_usd > 0
    assert response.latency_ms >= 0
    assert response.finish_reason == "stop"


async def test_sends_messages_in_the_provider_format(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = make_provider()
    sent = patch_create(monkeypatch, provider, completion())

    await provider.complete(MESSAGES)

    assert sent[0]["messages"] == [
        {"role": "system", "content": "sistema"},
        {"role": "user", "content": "pergunta"},
    ]
    assert "response_format" not in sent[0]


async def test_json_mode_is_requested_when_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = make_provider()
    sent = patch_create(monkeypatch, provider, completion())

    await provider.complete(MESSAGES, json_mode=True)

    assert sent[0]["response_format"] == {"type": "json_object"}


async def test_overrides_reach_the_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = make_provider()
    sent = patch_create(monkeypatch, provider, completion())

    await provider.complete(MESSAGES, temperature=0.9, max_output_tokens=64)

    assert sent[0]["temperature"] == 0.9
    assert sent[0]["max_tokens"] == 64


async def test_empty_choices_yield_empty_content(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resposta sem escolhas nao pode virar IndexError tres camadas acima."""
    provider = make_provider()
    empty = SimpleNamespace(model="gpt-4o-mini", choices=[], usage=None)
    patch_create(monkeypatch, provider, empty)

    response = await provider.complete(MESSAGES)

    assert response.content == ""
    assert response.usage.total_tokens == 0


@pytest.mark.parametrize(
    ("sdk_error", "expected"),
    [
        (openai.APITimeoutError(request=_REQUEST), LLMTimeoutError),
        (
            openai.RateLimitError("limite", response=_response(429), body=None),
            LLMRateLimitError,
        ),
        (
            openai.AuthenticationError("chave invalida", response=_response(401), body=None),
            LLMAuthenticationError,
        ),
        (
            openai.APIStatusError("servidor", response=_response(500), body=None),
            LLMError,
        ),
        (openai.APIConnectionError(request=_REQUEST), LLMError),
    ],
)
async def test_translates_sdk_errors(
    monkeypatch: pytest.MonkeyPatch, sdk_error: Exception, expected: type[LLMError]
) -> None:
    provider = make_provider()
    patch_create(monkeypatch, provider, sdk_error)

    with pytest.raises(expected):
        await provider.complete(MESSAGES)


async def test_authentication_failure_is_not_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = make_provider()
    patch_create(
        monkeypatch,
        provider,
        openai.AuthenticationError("chave invalida", response=_response(401), body=None),
    )

    with pytest.raises(LLMAuthenticationError) as exc_info:
        await provider.complete(MESSAGES)

    assert exc_info.value.retryable is False
