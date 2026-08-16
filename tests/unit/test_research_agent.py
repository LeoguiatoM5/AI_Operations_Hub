"""Testes do agente de pesquisa.

O foco e a ancoragem: uma resposta so vale se as fontes que ela cita existirem de fato.
"""

import json

import pytest
from pydantic import ValidationError

from app.agents.research import ResearchAgent, ResearchAnswer, format_context
from app.llm.exceptions import LLMResponseFormatError
from app.llm.fake_provider import FakeLLMProvider
from app.rag.base import SearchHit
from tests.conftest import research_json

PERGUNTA = "Qual o prazo para solicitar reembolso?"

TRECHOS = [
    SearchHit(
        chunk_id="c1",
        document_id="doc-1",
        text="Solicitacoes de reembolso em ate 30 dias corridos.",
        score=0.81,
        metadata={"filename": "politica.md"},
    ),
    SearchHit(
        chunk_id="c2",
        document_id="doc-2",
        text="A analise leva 5 dias uteis apos o protocolo.",
        score=0.62,
        metadata={"filename": "runbook.md"},
    ),
]


# ---------------------------------------------------------------- contexto


def test_context_is_numbered_for_citation() -> None:
    contexto = format_context(TRECHOS)

    assert contexto.startswith("[1]")
    assert "[2]" in contexto


def test_context_shows_the_source_file() -> None:
    """O modelo precisa saber de onde veio cada trecho para poder atribuir a resposta."""
    contexto = format_context(TRECHOS)

    assert "politica.md" in contexto
    assert "runbook.md" in contexto


# ---------------------------------------------------------------- ancoragem


async def test_answers_citing_the_source() -> None:
    provider = FakeLLMProvider(script=[research_json(citations=[1])])

    resultado = await ResearchAgent(provider).run(PERGUNTA, TRECHOS)

    assert resultado.payload.answered is True
    assert resultado.payload.citations == [1]
    assert resultado.repairs == 0


async def test_invented_citation_is_rejected_and_repaired() -> None:
    """O nucleo do anti-alucinacao: citar [7] com dois trechos e erro de validacao.

    O schema e construido por consulta, com as citacoes limitadas ao intervalo real. Sem
    isso, a citacao inventada passaria e so seria descoberta por quem lesse a resposta.
    """
    inventada = research_json(citations=[7])
    provider = FakeLLMProvider(script=[inventada, research_json(citations=[1])])

    resultado = await ResearchAgent(provider).run(PERGUNTA, TRECHOS)

    assert resultado.repairs == 1
    assert resultado.payload.citations == [1]


async def test_repair_prompt_mentions_the_citation_limit() -> None:
    provider = FakeLLMProvider(script=[research_json(citations=[9]), research_json()])

    await ResearchAgent(provider).run(PERGUNTA, TRECHOS)

    reparo = provider.calls[1][-1].content
    assert "citations" in reparo


async def test_persistent_invented_citation_fails_the_request() -> None:
    provider = FakeLLMProvider(script=[research_json(citations=[99])])

    with pytest.raises(LLMResponseFormatError):
        await ResearchAgent(provider).run(PERGUNTA, TRECHOS)


async def test_zero_is_not_a_valid_citation() -> None:
    """As citacoes comecam em 1, como aparecem no contexto."""
    provider = FakeLLMProvider(script=[research_json(citations=[0])])

    with pytest.raises(LLMResponseFormatError):
        await ResearchAgent(provider).run(PERGUNTA, TRECHOS)


# ---------------------------------------------------------------- coerencia


def test_answer_without_sources_is_incoherent() -> None:
    """Afirmar que respondeu sem apontar a origem significa inventar ou nao saber."""
    with pytest.raises(ValidationError, match="citacao"):
        ResearchAnswer.model_validate(
            {
                "answered": True,
                "answer": "O prazo e de 30 dias.",
                "citations": [],
                "confidence": 0.9,
            }
        )


def test_declining_to_answer_must_not_cite() -> None:
    with pytest.raises(ValidationError, match="nao deve citar"):
        ResearchAnswer.model_validate(
            {
                "answered": False,
                "answer": "Nao encontrei essa informacao.",
                "citations": [1],
                "confidence": 0.2,
            }
        )


def test_declining_to_answer_is_valid() -> None:
    """Admitir que a base nao cobre a pergunta e uma resposta correta, nao uma falha."""
    resposta = ResearchAnswer.model_validate(
        {
            "answered": False,
            "answer": "Os trechos nao mencionam prazo de reembolso.",
            "citations": [],
            "confidence": 0.1,
        }
    )

    assert resposta.answered is False


async def test_agent_requires_context() -> None:
    with pytest.raises(ValueError, match="contexto"):
        await ResearchAgent(FakeLLMProvider()).run(PERGUNTA, [])


async def test_context_reaches_the_model() -> None:
    provider = FakeLLMProvider(script=[research_json()])

    await ResearchAgent(provider).run(PERGUNTA, TRECHOS)

    system_prompt = provider.calls[0][0].content
    assert "30 dias corridos" in system_prompt
    assert "5 dias uteis" in system_prompt


async def test_schema_in_the_prompt_declares_the_citation_range() -> None:
    provider = FakeLLMProvider(script=[research_json()])

    await ResearchAgent(provider).run(PERGUNTA, TRECHOS)

    schema = json.loads(provider.calls[0][0].content.split("```json")[-1].split("```")[0].strip())
    citations = schema["properties"]["citations"]["items"]
    assert citations["maximum"] == 2
