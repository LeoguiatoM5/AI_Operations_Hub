"""Testes da fronteira entre texto do LLM e objeto tipado."""

import json

import pytest
from pydantic import BaseModel, Field

from app.llm.base import LLMMessage
from app.llm.exceptions import LLMResponseFormatError, LLMTimeoutError
from app.llm.fake_provider import FakeLLMProvider
from app.llm.structured import complete_structured, dump_schema, strip_code_fences

MENSAGENS = [LLMMessage.user("classifique isto")]


class Classificacao(BaseModel):
    categoria: str
    prioridade: int = Field(ge=1, le=5)


VALIDO = json.dumps({"categoria": "incidente", "prioridade": 3})


async def test_parses_a_valid_response() -> None:
    resultado = await complete_structured(
        FakeLLMProvider(script=[VALIDO]), MENSAGENS, Classificacao
    )

    assert resultado.value.categoria == "incidente"
    assert resultado.repairs == 0


async def test_repairs_once_after_broken_json() -> None:
    provider = FakeLLMProvider(script=["{isto nao fecha", VALIDO])

    resultado = await complete_structured(provider, MENSAGENS, Classificacao)

    assert resultado.value.prioridade == 3
    assert resultado.repairs == 1
    assert provider.call_count == 2


async def test_repair_prompt_contains_the_validation_error() -> None:
    """Dizer o que quebrou tem chance muito maior de corrigir do que apenas repetir."""
    fora_do_intervalo = json.dumps({"categoria": "incidente", "prioridade": 99})
    provider = FakeLLMProvider(script=[fora_do_intervalo, VALIDO])

    await complete_structured(provider, MENSAGENS, Classificacao)

    mensagem_de_reparo = provider.calls[1][-1].content
    assert "prioridade" in mensagem_de_reparo


async def test_gives_up_after_the_configured_attempts() -> None:
    provider = FakeLLMProvider(script=["nunca valido"])

    with pytest.raises(LLMResponseFormatError):
        await complete_structured(provider, MENSAGENS, Classificacao, repair_attempts=2)

    assert provider.call_count == 3  # a original mais dois reparos


async def test_no_repair_when_disabled() -> None:
    provider = FakeLLMProvider(script=["invalido", VALIDO])

    with pytest.raises(LLMResponseFormatError):
        await complete_structured(provider, MENSAGENS, Classificacao, repair_attempts=0)

    assert provider.call_count == 1


async def test_cost_is_summed_across_attempts() -> None:
    """Cada reparo e uma chamada paga a mais."""
    uma = await complete_structured(FakeLLMProvider(script=[VALIDO]), MENSAGENS, Classificacao)
    duas = await complete_structured(
        FakeLLMProvider(script=["quebrado", VALIDO]), MENSAGENS, Classificacao
    )

    assert duas.response.usage.total_tokens > uma.response.usage.total_tokens
    assert duas.response.attempts == 2


async def test_provider_errors_are_not_swallowed() -> None:
    """Falha de rede nao e problema de formato: precisa subir como veio."""
    with pytest.raises(LLMTimeoutError):
        await complete_structured(
            FakeLLMProvider(script=[LLMTimeoutError()]), MENSAGENS, Classificacao
        )


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ('{"a": 1}', '{"a": 1}'),
        ('```json\n{"a": 1}\n```', '{"a": 1}'),
        ('```\n{"a": 1}\n```', '{"a": 1}'),
        ('  {"a": 1}  ', '{"a": 1}'),
    ],
)
def test_strips_markdown_fences(entrada: str, esperado: str) -> None:
    assert strip_code_fences(entrada) == esperado


def test_schema_is_serializable_for_the_prompt() -> None:
    """Entregar o schema ao modelo funciona melhor que descrever o formato em prosa."""
    schema = json.loads(dump_schema(Classificacao))

    assert "categoria" in schema["properties"]
    assert schema["properties"]["prioridade"]["maximum"] == 5
