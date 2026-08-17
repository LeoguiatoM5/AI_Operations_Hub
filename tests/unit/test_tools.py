"""Testes das ferramentas concretas e do catalogo real montado pela fabrica."""

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.rag.base import Chunk
from app.rag.embeddings import FakeEmbeddingProvider
from app.rag.memory_store import InMemoryVectorStore
from app.rag.retriever import Retriever
from app.tools.base import ToolScope
from app.tools.factory import build_tool_registry
from app.tools.knowledge import SearchKnowledgeTool
from app.tools.notify import MemoryNotifier, NotifyInput, NotifyTool
from app.tools.registry import TOOL_NAME_PATTERN

BASE = {
    "reembolso": "Politica de reembolso: solicitacoes em ate 30 dias corridos.",
    "ferias": "Politica de ferias: periodo aquisitivo de doze meses de trabalho.",
}


@pytest.fixture
async def retriever() -> Retriever:
    embedder = FakeEmbeddingProvider()
    store = InMemoryVectorStore()
    chunks = [
        Chunk(id=nome, document_id=f"doc-{nome}", text=texto, index=0, metadata={"filename": nome})
        for nome, texto in BASE.items()
    ]
    vetores = (await embedder.embed_documents([chunk.text for chunk in chunks])).vectors
    await store.add_chunks(chunks, vetores)
    return Retriever(embedder, store)


@pytest.fixture
def notifier() -> MemoryNotifier:
    return MemoryNotifier()


# --------------------------------------------------------------------- notificacao


def test_notification_is_a_write_action(notifier: MemoryNotifier) -> None:
    ferramenta = NotifyTool(notifier)

    assert ferramenta.scope is ToolScope.WRITE
    assert ferramenta.scope.requires_approval is True


async def test_notification_delivers_the_message(notifier: MemoryNotifier) -> None:
    ferramenta = NotifyTool(notifier)

    resultado = await ferramenta.run(
        NotifyInput(title="Chamados criticos", body="Tres em aberto.", channel="operacoes")
    )

    assert notifier.messages[0].body == "Tres em aberto."
    assert resultado.output["channel"] == "operacoes"
    assert "operacoes" in resultado.summary


@pytest.mark.parametrize(
    "campos",
    [
        {"title": "", "body": "conteudo"},
        {"title": "titulo", "body": ""},
        {"title": "t" * 200, "body": "conteudo"},
        {"title": "titulo", "body": "b" * 5_000},
    ],
)
def test_notification_payload_has_limits(campos: dict[str, str]) -> None:
    """O payload e gerado por LLM: sem teto, uma alucinacao longa vira mensagem gigante
    em um canal de equipe."""
    with pytest.raises(PydanticValidationError):
        NotifyInput(**campos)


# --------------------------------------------------------------------- conhecimento


def test_search_is_a_read_action(retriever: Retriever) -> None:
    ferramenta = SearchKnowledgeTool(retriever)

    assert ferramenta.scope is ToolScope.READ
    assert ferramenta.scope.requires_approval is False


async def test_search_returns_sources(retriever: Retriever) -> None:
    ferramenta = SearchKnowledgeTool(retriever)

    resultado = await ferramenta.run(
        ferramenta.input_model(query="qual o prazo para solicitar reembolso?")
    )

    assert resultado.output["found"] >= 1
    assert resultado.output["hits"][0]["document_id"] == "doc-reembolso"


async def test_empty_coverage_is_a_result_not_an_error() -> None:
    """Mesma regra do no de pesquisa: base sem cobertura e resposta honesta, e uma
    excecao aqui empurraria o grafo para o caminho de degradacao sem que nada falhasse."""
    retriever = Retriever(FakeEmbeddingProvider(), InMemoryVectorStore())
    ferramenta = SearchKnowledgeTool(retriever)

    resultado = await ferramenta.run(ferramenta.input_model(query="qualquer assunto"))

    assert resultado.output["found"] == 0
    assert "nao cobre" in resultado.summary


def test_search_caps_top_k(retriever: Retriever) -> None:
    """`top_k` vem de um LLM: um valor alucinado em 500 custaria uma consulta enorme."""
    ferramenta = SearchKnowledgeTool(retriever)

    with pytest.raises(PydanticValidationError):
        ferramenta.input_model(query="reembolso", top_k=500)


# --------------------------------------------------------------------- catalogo real


async def test_real_catalog_has_both_scopes(retriever: Retriever) -> None:
    """Um catalogo so de leitura tornaria a regra de aprovacao letra morta."""
    registry = build_tool_registry(retriever=retriever, notifier=MemoryNotifier())

    escopos = {spec.scope for spec in registry.specs()}

    assert escopos == {ToolScope.READ, ToolScope.WRITE}


async def test_every_registered_tool_is_well_formed(retriever: Retriever) -> None:
    """Contrato aplicado ao catalogo inteiro: uma ferramenta nova nasce coberta por este
    teste sem que ninguem precise lembrar de escrever outro."""
    registry = build_tool_registry(retriever=retriever, notifier=MemoryNotifier())

    for spec in registry.specs():
        assert TOOL_NAME_PATTERN.fullmatch(spec.name), spec.name
        assert spec.description.strip(), spec.name
        assert spec.requires_approval is (spec.scope is ToolScope.WRITE), spec.name
        assert spec.input_schema.get("properties"), spec.name
