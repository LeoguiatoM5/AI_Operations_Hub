"""Testes da coerencia interna da classificacao de triagem."""

import json

import pytest
from pydantic import ValidationError

from app.agents.triage import TriageAgent, TriageResult
from app.llm.fake_provider import FakeLLMProvider

BASE = {
    "intent": "analise",
    "summary": "Resumo.",
    "entities": [],
    "urgency": "media",
    "requires_approval": False,
    "suggested_agents": ["research"],
    "confidence": 0.7,
}


def test_read_only_request_needs_no_automation_agent() -> None:
    resultado = TriageResult.model_validate(BASE)

    assert resultado.requires_approval is False


def test_write_request_must_include_the_automation_agent() -> None:
    """Contradicao observada em execucao real: aprovacao exigida, mas nenhum agente."""
    with pytest.raises(ValidationError, match="automation"):
        TriageResult.model_validate({**BASE, "requires_approval": True, "suggested_agents": []})


def test_write_request_is_accepted_when_coherent() -> None:
    resultado = TriageResult.model_validate(
        {
            **BASE,
            "intent": "automacao",
            "requires_approval": True,
            "suggested_agents": ["automation"],
        }
    )

    assert resultado.suggested_agents == ["automation"]


def test_confidence_outside_the_range_is_rejected() -> None:
    with pytest.raises(ValidationError):
        TriageResult.model_validate({**BASE, "confidence": 1.4})


async def test_agent_repairs_an_incoherent_classification() -> None:
    """O retry dirigido corrige semantica, nao apenas sintaxe."""
    incoerente = json.dumps({**BASE, "requires_approval": True, "suggested_agents": []})
    coerente = json.dumps(
        {
            **BASE,
            "intent": "automacao",
            "requires_approval": True,
            "suggested_agents": ["automation"],
        }
    )
    provider = FakeLLMProvider(script=[incoerente, coerente])

    resultado = await TriageAgent(provider).run("envie um e-mail")

    assert resultado.repairs == 1
    assert resultado.payload.suggested_agents == ["automation"]


async def test_repair_prompt_explains_the_incoherence() -> None:
    incoerente = json.dumps({**BASE, "requires_approval": True, "suggested_agents": []})
    coerente = json.dumps(
        {
            **BASE,
            "intent": "automacao",
            "requires_approval": True,
            "suggested_agents": ["automation"],
        }
    )
    provider = FakeLLMProvider(script=[incoerente, coerente])

    await TriageAgent(provider).run("envie um e-mail")

    mensagem_de_reparo = provider.calls[1][-1].content
    assert "automation" in mensagem_de_reparo
    assert "objeto" in mensagem_de_reparo  # erro de coerencia nao pertence a um campo
