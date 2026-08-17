"""Testes do agente de automacao.

O foco esta na validacao contra o CATALOGO -- a regra que o JSON Schema nao consegue
expressar, porque quais ferramentas existem e o que cada uma aceita e dado de runtime.

Essa validacao entra no `complete_structured` como validador, e nao depois dele, e a
diferenca aparece nos testes de reparo: uma escolha incoerente vira uma segunda tentativa
com o motivo exato da rejeicao, em vez de virar falha do no.
"""

import json
from typing import Any

import pytest

from app.agents.automation import AutomationAgent, ToolCall
from app.llm.exceptions import LLMResponseFormatError
from app.llm.fake_provider import FakeLLMProvider
from app.rag.embeddings import FakeEmbeddingProvider
from app.rag.memory_store import InMemoryVectorStore
from app.rag.retriever import Retriever
from app.tools.factory import build_tool_registry
from app.tools.notify import MemoryNotifier
from app.tools.registry import ToolRegistry


@pytest.fixture
def registry() -> ToolRegistry:
    retriever = Retriever(FakeEmbeddingProvider(), InMemoryVectorStore())
    return build_tool_registry(retriever=retriever, notifier=MemoryNotifier())


def chamada(**overrides: Any) -> str:
    base: dict[str, Any] = {
        "tool": "send_notification",
        "arguments": {"title": "Aviso", "body": "Tres chamados criticos.", "channel": "operacoes"},
        "reason": "O pedido e avisar o time.",
    }
    return json.dumps({**base, **overrides}, ensure_ascii=False)


# ---------------------------------------------------------------- caminho feliz


async def test_chooses_a_tool_and_builds_its_arguments(registry: ToolRegistry) -> None:
    provider = FakeLLMProvider(script=[chamada()])
    agente = AutomationAgent(provider, registry)

    resultado = await agente.run("Avise o time de operacoes.")

    assert isinstance(resultado.payload, ToolCall)
    assert resultado.payload.tool == "send_notification"
    assert resultado.payload.arguments["channel"] == "operacoes"
    assert resultado.repairs == 0


async def test_the_catalog_goes_into_the_prompt(registry: ToolRegistry) -> None:
    """O modelo nao adivinha o que existe: o catalogo e entregue a ele, com schema."""
    provider = FakeLLMProvider(script=[chamada()])

    await AutomationAgent(provider, registry).run("Avise o time.")

    prompt = provider.calls[0][0].content
    assert "send_notification" in prompt
    assert "search_knowledge_base" in prompt
    assert "input_schema" in prompt


async def test_the_prompt_says_which_tools_need_approval(registry: ToolRegistry) -> None:
    provider = FakeLLMProvider(script=[chamada()])

    await AutomationAgent(provider, registry).run("Avise o time.")

    assert "requires_approval" in provider.calls[0][0].content


# ---------------------------------------------------------------- reparo dirigido


async def test_an_invented_tool_is_repaired_not_executed(registry: ToolRegistry) -> None:
    """Alucinar um nome de ferramenta e o erro mais provavel deste agente."""
    provider = FakeLLMProvider(
        script=[chamada(tool="enviar_email_urgente"), chamada()], repeat_last=False
    )

    resultado = await AutomationAgent(provider, registry).run("Avise o time.")

    assert resultado.payload.tool == "send_notification"
    assert resultado.repairs == 1


async def test_the_repair_message_lists_the_real_tools(registry: ToolRegistry) -> None:
    """Dizer "invalido" nao ajuda o modelo. Dizer o que existe, ajuda."""
    provider = FakeLLMProvider(script=[chamada(tool="enviar_email"), chamada()], repeat_last=False)

    await AutomationAgent(provider, registry).run("Avise o time.")

    reparo = provider.calls[1][-1].content
    assert "enviar_email" in reparo
    assert "send_notification" in reparo


async def test_arguments_outside_the_schema_are_repaired(registry: ToolRegistry) -> None:
    provider = FakeLLMProvider(
        script=[chamada(arguments={"title": "Aviso"}), chamada()], repeat_last=False
    )

    resultado = await AutomationAgent(provider, registry).run("Avise o time.")

    assert resultado.payload.arguments["body"]
    assert resultado.repairs == 1


async def test_the_repair_message_says_which_argument_failed(registry: ToolRegistry) -> None:
    provider = FakeLLMProvider(
        script=[chamada(arguments={"title": "Aviso"}), chamada()], repeat_last=False
    )

    await AutomationAgent(provider, registry).run("Avise o time.")

    assert "body" in provider.calls[1][-1].content


async def test_giving_up_raises_instead_of_returning_something_invalid(
    registry: ToolRegistry,
) -> None:
    """Depois dos reparos, a saida do agente ou e executavel ou e erro. Nunca um meio-termo."""
    provider = FakeLLMProvider(script=[chamada(tool="nao_existe")])

    with pytest.raises(LLMResponseFormatError):
        await AutomationAgent(provider, registry).run("Avise o time.")


async def test_repair_attempts_are_paid_and_reported(registry: ToolRegistry) -> None:
    """Uma tentativa de reparo e uma segunda chamada paga: some no custo da execucao."""
    provider = FakeLLMProvider(script=[chamada(tool="inexistente"), chamada()], repeat_last=False)

    resultado = await AutomationAgent(provider, registry).run("Avise o time.")

    assert resultado.response.attempts == 2
    assert resultado.response.usage.prompt_tokens > 0
