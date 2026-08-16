"""Testes do provider falso.

Ele e infraestrutura de teste, mas tambem e o provider padrao da aplicacao -- entao
precisa ser confiavel como qualquer outro componente.
"""

import json

import pytest

from app.llm.base import LLMMessage, LLMProvider
from app.llm.exceptions import LLMRateLimitError
from app.llm.fake_provider import FakeLLMProvider

MESSAGES = [LLMMessage.system("Voce e util."), LLMMessage.user("Qual a capital do Brasil?")]


def test_satisfies_the_provider_protocol() -> None:
    """Structural typing: nao ha heranca, a conformidade e verificada pela forma."""
    assert isinstance(FakeLLMProvider(), LLMProvider)


async def test_same_input_produces_same_output() -> None:
    """Determinismo e o que torna a suite reproduzivel e as avaliacoes comparaveis."""
    first = await FakeLLMProvider().complete(MESSAGES)
    second = await FakeLLMProvider().complete(MESSAGES)

    assert first.content == second.content


async def test_different_input_produces_different_output() -> None:
    provider = FakeLLMProvider()

    first = await provider.complete(MESSAGES)
    second = await provider.complete([LLMMessage.user("Outra pergunta completamente diferente.")])

    assert first.content != second.content


async def test_default_answer_is_valid_json() -> None:
    response = await FakeLLMProvider().complete(MESSAGES)

    payload = json.loads(response.content)
    assert "answer" in payload


async def test_script_is_consumed_in_order() -> None:
    provider = FakeLLMProvider(script=["primeira", "segunda"])

    assert (await provider.complete(MESSAGES)).content == "primeira"
    assert (await provider.complete(MESSAGES)).content == "segunda"
    assert (await provider.complete(MESSAGES)).content == "segunda"  # repete a ultima
    assert provider.call_count == 3


async def test_script_can_raise_provider_errors() -> None:
    """Cenario que a API real nao produz sob encomenda: um 429 na hora que queremos."""
    provider = FakeLLMProvider(script=[LLMRateLimitError()])

    with pytest.raises(LLMRateLimitError):
        await provider.complete(MESSAGES)


async def test_records_calls_for_inspection() -> None:
    provider = FakeLLMProvider()

    await provider.complete(MESSAGES)

    assert provider.calls[0][1].content == "Qual a capital do Brasil?"


async def test_reports_usage_and_zero_cost() -> None:
    response = await FakeLLMProvider().complete(MESSAGES)

    assert response.usage.total_tokens > 0
    assert response.cost_usd == 0.0
    assert response.provider == "fake"
    assert response.attempts == 1
