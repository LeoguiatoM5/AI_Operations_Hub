"""Testes do provider falso ciente de schema.

Existem por causa de um buraco que a CI revelou: `LLM_PROVIDER=fake` subia a aplicacao mas
derrubava qualquer fluxo real com `llm_response_format_error`. A promessa de "roda sem
chave de API" valia para o startup, e nao para exercitar o sistema.

O teste que mais importa aqui percorre **todos os schemas do projeto** e exige que o
provider falso produza instancia valida para cada um. Um schema novo com validador de
coerencia nasce coberto.
"""

import json

import pytest
from pydantic import BaseModel

from app.agents.analysis import AnalysisResult
from app.agents.automation import ToolCall
from app.agents.reporter import Report
from app.agents.triage import TriageResult
from app.llm.base import LLMMessage
from app.llm.fake_provider import FakeLLMProvider
from app.llm.fake_schema import extract_schema, instance_for
from app.llm.structured import dump_schema
from app.quality.completeness import CompletenessVerdict
from app.quality.consistency import ConsistencyVerdict
from app.quality.grounding import GroundingVerdict
from app.quality.relevance import RelevanceVerdict

TODOS_OS_SCHEMAS = [
    TriageResult,
    AnalysisResult,
    Report,
    ToolCall,
    GroundingVerdict,
    RelevanceVerdict,
    CompletenessVerdict,
    ConsistencyVerdict,
]


@pytest.mark.parametrize("modelo", TODOS_OS_SCHEMAS, ids=lambda m: m.__name__)
def test_every_schema_of_the_project_gets_a_valid_instance(modelo: type[BaseModel]) -> None:
    """Vale para os validadores de coerencia tambem (ED-028), e nao so para os tipos:
    `TriageResult` rejeita `requires_approval` sem `automation`, e `AnalysisResult` rejeita
    confianca alta sem achado."""
    gerado = instance_for(modelo.model_json_schema())

    modelo.model_validate(gerado)


@pytest.mark.parametrize("modelo", TODOS_OS_SCHEMAS, ids=lambda m: m.__name__)
async def test_the_fake_provider_answers_any_agent_prompt(modelo: type[BaseModel]) -> None:
    """O caminho de verdade: o schema chega ao provider dentro do prompt, como os agentes
    o enviam."""
    provider = FakeLLMProvider()
    prompt = f"Instrucoes do agente.\n\n```json\n{dump_schema(modelo)}\n```"

    resposta = await provider.complete([LLMMessage.system(prompt), LLMMessage.user("faca")])

    modelo.model_validate_json(resposta.content)


async def test_without_a_schema_the_old_format_is_kept() -> None:
    """Testes antigos dependem do formato generico; mudar tudo de uma vez seria trocar um
    buraco por outro."""
    provider = FakeLLMProvider()

    resposta = await provider.complete([LLMMessage.user("uma pergunta qualquer")])

    corpo = json.loads(resposta.content)
    assert "fingerprint" in corpo


async def test_the_answer_stays_deterministic() -> None:
    """A razao de o provider falso existir: mesma entrada, mesma saida."""
    prompt = f"```json\n{dump_schema(TriageResult)}\n```"
    provider = FakeLLMProvider()

    primeira = await provider.complete([LLMMessage.system(prompt), LLMMessage.user("x")])
    segunda = await provider.complete([LLMMessage.system(prompt), LLMMessage.user("x")])

    assert primeira.content == segunda.content


# ---------------------------------------------------------------- o gerador


def test_a_prompt_without_schema_returns_nothing() -> None:
    assert extract_schema("texto sem bloco de codigo") is None


def test_a_fenced_block_that_is_not_a_schema_is_ignored() -> None:
    assert extract_schema('```json\n{"apenas": "dados"}\n```') is None


def test_enums_take_the_first_allowed_value() -> None:
    gerado = instance_for({"properties": {"cor": {"enum": ["azul", "verde"]}}, "required": ["cor"]})

    assert gerado["cor"] == "azul"


def test_minimum_length_is_respected() -> None:
    """`Report.executive_summary` exige `min_length=1`; um campo curto demais reprovaria."""
    gerado = instance_for(
        {"properties": {"t": {"type": "string", "minLength": 40}}, "required": ["t"]}
    )

    assert len(gerado["t"]) >= 40


def test_maximum_length_is_respected() -> None:
    gerado = instance_for(
        {"properties": {"t": {"type": "string", "maxLength": 5}}, "required": ["t"]}
    )

    assert len(gerado["t"]) <= 5


def test_arrays_come_with_one_item() -> None:
    """Lista vazia quebraria os validadores que exigem conteudo -- evidencia de um achado,
    limitacoes de um relatorio vazio."""
    gerado = instance_for({"properties": {"itens": {"type": "array", "items": {"type": "string"}}}})

    assert len(gerado["itens"]) == 1


def test_requires_approval_is_false_to_stay_coherent() -> None:
    """`requires_approval=true` exigiria `automation` no plano, e essa regra vive num
    validador Python que o schema nao expressa."""
    gerado = instance_for(
        {
            "properties": {"requires_approval": {"type": "boolean"}},
            "required": ["requires_approval"],
        }
    )

    assert gerado["requires_approval"] is False


def test_confidence_is_not_high() -> None:
    """Confianca alta sem conteudo reprova em `AnalysisResult`."""
    gerado = instance_for({"properties": {"confidence": {"type": "number"}}})

    assert gerado["confidence"] < 0.7
