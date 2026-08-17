"""Testes do servidor MCP.

O teste mais importante deste arquivo afirma que uma ferramenta **nao existe**. Ver
`test_there_is_no_tool_to_approve_an_action`.

Os demais cobram a promessa do V1: se `services/` e mesmo transporte-agnostica, o servidor
MCP entrega os mesmos resultados que a API REST sem uma linha de regra propria.
"""

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.llm.fake_provider import FakeLLMProvider
from app.rag.embeddings import FakeEmbeddingProvider
from app.rag.memory_store import InMemoryVectorStore
from app.services.document_service import DocumentService
from app.tools.notify import MemoryNotifier
from mcp_server.container import ServiceContainer, build_container
from mcp_server.server import build_server
from tests.conftest import research_json, triage_json

RELATORIO = json.dumps(
    {
        "executive_summary": "Dois chamados repetem falha de login.",
        "key_points": ["Falha de login em dois chamados"],
        "recommendations": [],
        "limitations": [],
        "confidence": 0.8,
    },
    ensure_ascii=False,
)


@pytest.fixture
def mcp_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"vector_store": "memory", "notifier": "memory"})


@pytest.fixture
async def container(mcp_settings: Settings, engine: AsyncEngine) -> AsyncIterator[ServiceContainer]:
    async with build_container(
        mcp_settings,
        engine=engine,
        llm=FakeLLMProvider(script=[triage_json()]),
        embedder=FakeEmbeddingProvider(),
        store=InMemoryVectorStore(),
        notifier=MemoryNotifier(),
        checkpointer=MemorySaver(),
    ) as instancia:
        yield instancia


async def chamar(container: ServiceContainer, nome: str, **argumentos: Any) -> dict[str, Any]:
    """Chama uma ferramenta pelo protocolo, como um cliente faria."""
    resultado = await build_server(container).call_tool(nome, argumentos)
    # O FastMCP devolve (conteudo, dados_estruturados); interessa o segundo.
    estruturado = resultado[1] if isinstance(resultado, tuple) else resultado
    return dict(estruturado) if isinstance(estruturado, dict) else {"raw": estruturado}


async def indexar(container: ServiceContainer, texto: bytes, nome: str) -> None:
    async with container.session() as session:
        await DocumentService(
            container.documents(session),
            container.embedder,
            container.store,
            chunk_size=container.settings.chunk_size,
            chunk_overlap=container.settings.chunk_overlap,
            max_size_bytes=container.settings.max_upload_bytes,
        ).ingest(texto, filename=nome)


# ---------------------------------------------------------------- a fronteira


async def test_there_is_no_tool_to_approve_an_action(container: ServiceContainer) -> None:
    """**O teste mais importante do V6.**

    Um cliente MCP e um modelo de linguagem. Dar a ele a ferramenta de aprovar
    significaria a IA autorizando a propria acao -- exatamente o que o V4 inteiro existe
    para impedir. A decisao humana so pode ser dada pela interface humana.
    """
    nomes = {item.name for item in await build_server(container).list_tools()}

    proibidas = {nome for nome in nomes if "approve" in nome or "reject" in nome}
    assert proibidas == set(), f"o servidor MCP nao pode oferecer decisao humana: {proibidas}"


async def test_pending_approvals_are_visible_but_not_decidable(
    container: ServiceContainer,
) -> None:
    """Ver o que esta pendente e util -- relatar a quem decide e o papel do modelo."""
    resposta = await chamar(container, "list_pending_approvals")

    assert "approvals" in resposta
    assert "POST /approvals" in resposta["decide_at"], "precisa dizer onde a decisao acontece"


async def test_a_write_action_stops_without_executing(container: ServiceContainer) -> None:
    """O mesmo invariante do V4, pelo outro transporte: nada e executado sem uma pessoa."""
    container.llm = FakeLLMProvider(  # type: ignore[assignment]
        script=[
            triage_json(suggested_agents=["automation"], requires_approval=True),
            json.dumps(
                {
                    "tool": "send_notification",
                    "arguments": {"title": "Aviso", "body": "Corpo.", "channel": "ops"},
                    "reason": "O pedido e avisar o time.",
                }
            ),
        ],
        repeat_last=False,
    )

    resposta = await chamar(container, "run_workflow", task="Avise o time de operacoes.")

    assert resposta["status"] == "waiting_approval"
    assert resposta["pending_approval"]["tool"] == "send_notification"
    assert "nao ha ferramenta MCP" in resposta["pending_approval"]["note"]
    assert container.notifier.sent == [], "nada pode ter sido enviado"  # type: ignore[union-attr]


# ---------------------------------------------------------------- catalogo


async def test_the_server_publishes_its_tools(container: ServiceContainer) -> None:
    nomes = {item.name for item in await build_server(container).list_tools()}

    assert nomes == {
        "search_knowledge_base",
        "list_documents",
        "get_execution",
        "list_pending_approvals",
        "run_workflow",
    }


async def test_every_tool_describes_itself(container: ServiceContainer) -> None:
    """A descricao e o prompt: e por ela que o modelo decide se a ferramenta serve."""
    for item in await build_server(container).list_tools():
        assert item.description and len(item.description) > 40, item.name
        assert item.inputSchema.get("properties") is not None, item.name


async def test_the_server_instructs_about_refusal_and_approval(
    container: ServiceContainer,
) -> None:
    """As duas propriedades que distinguem este sistema precisam chegar ao cliente."""
    instrucoes = build_server(container).instructions or ""

    assert "nao cobre" in instrucoes
    assert "decisao de uma pessoa" in instrucoes


# ---------------------------------------------------------------- leitura


async def test_the_knowledge_base_answers_with_sources(container: ServiceContainer) -> None:
    await indexar(container, b"O prazo para solicitar reembolso e de 30 dias corridos.", "p.md")
    container.llm = FakeLLMProvider(script=[research_json(citations=[1])])  # type: ignore[assignment]

    resposta = await chamar(
        container, "search_knowledge_base", question="Qual o prazo de reembolso?"
    )

    assert resposta["answered"] is True
    assert resposta["sources"][0]["document"] == "p.md"


async def test_an_uncovered_subject_answers_false_without_calling_the_llm(
    container: ServiceContainer,
) -> None:
    """A propriedade central do V2 atravessa o transporte intacta."""
    provider = FakeLLMProvider(script=[research_json()], repeat_last=False)
    container.llm = provider  # type: ignore[assignment]

    resposta = await chamar(container, "search_knowledge_base", question="Receita de bolo?")

    assert resposta["answered"] is False
    assert provider.call_count == 0, "sem contexto, nao se paga a chamada"


async def test_listing_documents_shows_what_the_base_covers(container: ServiceContainer) -> None:
    await indexar(container, b"Politica de ferias: doze meses de periodo aquisitivo.", "f.md")

    resposta = await chamar(container, "list_documents")

    assert resposta["total"] == 1
    assert resposta["documents"][0]["filename"] == "f.md"


async def test_an_unknown_execution_says_so_instead_of_failing(
    container: ServiceContainer,
) -> None:
    """Erro de protocolo obrigaria o modelo a interpretar excecao; um campo e mais claro."""
    resposta = await chamar(container, "get_execution", execution_id="nao-existe")

    assert resposta["found"] is False


async def test_an_execution_exposes_the_full_agent_chain(container: ServiceContainer) -> None:
    """E aqui que se responde 'por que o sistema concluiu isso?'."""
    container.llm = FakeLLMProvider(  # type: ignore[assignment]
        script=[triage_json(suggested_agents=[], requires_approval=False), RELATORIO],
        repeat_last=False,
    )
    execucao = await chamar(container, "run_workflow", task="Resuma a situacao dos chamados.")

    detalhe = await chamar(container, "get_execution", execution_id=execucao["execution_id"])

    assert detalhe["found"] is True
    assert [passo["agent"] for passo in detalhe["steps"]] == ["orchestrator", "reporter"]
    assert detalhe["cost_usd"] >= 0
