"""Testes do endpoint POST /rag/query."""

from collections.abc import Callable

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.llm.base import LLMProvider
from app.llm.exceptions import LLMTimeoutError
from app.llm.fake_provider import FakeLLMProvider
from app.main import create_app
from app.rag.embeddings import FakeEmbeddingProvider
from app.rag.memory_store import InMemoryVectorStore
from tests.conftest import research_json

POLITICA = (
    b"Politica de reembolso. Colaboradores podem solicitar reembolso de despesas em ate "
    b"30 dias corridos apos o gasto. A analise leva 5 dias uteis."
)

PERGUNTA = {"question": "Qual o prazo para solicitar reembolso de despesas?"}


@pytest.fixture
def provider() -> FakeLLMProvider:
    """Substitui o provider padrao da suite: aqui o LLM so recebe pedidos de pesquisa."""
    return FakeLLMProvider(script=[research_json(citations=[1])])


async def _ingerir(
    client: AsyncClient, nome: str = "politica.txt", conteudo: bytes = POLITICA
) -> AsyncClient:
    await client.post("/documents/upload", files={"file": (nome, conteudo, "text/plain")})
    return client


# ---------------------------------------------------------------- caminho feliz


async def test_answers_with_sources(client: AsyncClient) -> None:
    await _ingerir(client)

    response = await client.post("/rag/query", json=PERGUNTA)

    assert response.status_code == 200
    body = response.json()
    assert body["answered"] is True
    assert body["sources"]
    assert any(fonte["cited"] for fonte in body["sources"])


async def test_source_carries_the_origin_file(client: AsyncClient) -> None:
    """Resposta sem fonte rastreavel nao serve para uma decisao empresarial."""
    await _ingerir(client)

    fonte = (await client.post("/rag/query", json=PERGUNTA)).json()["sources"][0]

    assert fonte["filename"] == "politica.txt"
    assert fonte["document_id"]
    assert fonte["excerpt"]
    assert 0.0 <= fonte["score"] <= 1.0


async def test_reports_retrieval_details(client: AsyncClient) -> None:
    await _ingerir(client)

    body = (await client.post("/rag/query", json=PERGUNTA)).json()

    assert body["retrieval"]["chunks_retrieved"] >= 1
    assert body["retrieval"]["chunks_cited"] == 1
    assert body["retrieval"]["min_score"] >= 0
    assert body["usage"]["total_tokens"] > 0


async def test_can_restrict_the_search_to_one_document(client: AsyncClient) -> None:
    primeiro = (
        await client.post("/documents/upload", files={"file": ("a.txt", POLITICA, "text/plain")})
    ).json()
    await client.post(
        "/documents/upload",
        files={"file": ("b.txt", b"Politica de ferias com doze meses.", "text/plain")},
    )

    body = (
        await client.post(
            "/rag/query", json={**PERGUNTA, "document_ids": [primeiro["document_id"]]}
        )
    ).json()

    assert {fonte["document_id"] for fonte in body["sources"]} == {primeiro["document_id"]}


# ---------------------------------------------------------------- sem contexto


async def test_empty_knowledge_base_answers_honestly(
    client: AsyncClient, provider: FakeLLMProvider
) -> None:
    """Sem base, a resposta correta e admitir que nao ha o que consultar."""
    response = await client.post("/rag/query", json=PERGUNTA)

    assert response.status_code == 200
    body = response.json()
    assert body["answered"] is False
    assert body["sources"] == []
    assert provider.call_count == 0, "sem contexto, nao se gasta chamada de LLM"


async def test_irrelevant_question_does_not_reach_the_model(
    client: AsyncClient, provider: FakeLLMProvider
) -> None:
    """Chamar o LLM sem contexto seria convidar a alucinacao."""
    await _ingerir(client)

    body = (
        await client.post(
            "/rag/query", json={"question": "Qual a receita de bolo de cenoura com chocolate?"}
        )
    ).json()

    assert body["answered"] is False
    assert provider.call_count == 0


# ---------------------------------------------------------------- base misturada


class OutroModeloEmbedder(FakeEmbeddingProvider):
    """Mesmo algoritmo, outro nome de modelo -- basta para simular base misturada."""

    @property
    def model(self) -> str:
        return "outro-modelo-1"


async def test_mixed_embedding_models_are_refused(
    settings: Settings, engine: AsyncEngine, provider: FakeLLMProvider
) -> None:
    """Vetores de modelos diferentes nao sao comparaveis: recusar e melhor que devolver lixo.

    A busca nao daria erro: os numeros continuam entre 0 e 1 e os resultados continuam
    aparecendo. Eles e que seriam aleatorios -- falha silenciosa, a pior categoria.
    """
    store = InMemoryVectorStore()

    indexador = create_app(
        settings,
        engine=engine,
        llm_provider=provider,
        embedding_provider=FakeEmbeddingProvider(),
        vector_store=store,
    )
    consultante = create_app(
        settings,
        engine=engine,
        llm_provider=provider,
        embedding_provider=OutroModeloEmbedder(),
        vector_store=store,
    )

    async with AsyncClient(
        transport=ASGITransport(app=indexador), base_url="http://testserver"
    ) as cliente:
        await _ingerir(cliente)

    async with AsyncClient(
        transport=ASGITransport(app=consultante), base_url="http://testserver"
    ) as cliente:
        response = await cliente.post("/rag/query", json=PERGUNTA)

    assert response.status_code == 409
    erro = response.json()["error"]
    assert erro["code"] == "embedding_model_mismatch"
    assert erro["details"]["current_model"] == "outro-modelo-1"
    assert "fake-embedding-1" in erro["details"]["models_in_index"]


async def test_same_model_is_accepted(client: AsyncClient) -> None:
    """A guarda nao pode atrapalhar o caso normal."""
    await _ingerir(client)

    assert (await client.post("/rag/query", json=PERGUNTA)).status_code == 200


# ---------------------------------------------------------------- falhas e validacao


async def test_llm_failure_maps_to_an_honest_status(
    make_client: Callable[[LLMProvider], AsyncClient],
) -> None:
    cliente = make_client(FakeLLMProvider(script=[LLMTimeoutError()]))
    await _ingerir(cliente)

    response = await cliente.post("/rag/query", json=PERGUNTA)

    assert response.status_code == 504
    assert response.json()["error"]["code"] == "llm_timeout"


async def test_invented_citation_is_repaired_before_answering(
    make_client: Callable[[LLMProvider], AsyncClient],
) -> None:
    """Ponta a ponta: citacao fora do intervalo vira reparo, nao resposta entregue."""
    modelo = FakeLLMProvider(script=[research_json(citations=[9]), research_json(citations=[1])])
    cliente = make_client(modelo)
    await _ingerir(cliente)

    body = (await cliente.post("/rag/query", json=PERGUNTA)).json()

    assert body["repairs"] == 1
    assert body["answered"] is True


async def test_rejects_empty_question(client: AsyncClient) -> None:
    response = await client.post("/rag/query", json={"question": ""})

    assert response.status_code == 422


async def test_rejects_oversized_question(client: AsyncClient) -> None:
    response = await client.post("/rag/query", json={"question": "x" * 2_001})

    assert response.status_code == 422


async def test_rejects_invalid_top_k(client: AsyncClient) -> None:
    assert (await client.post("/rag/query", json={**PERGUNTA, "top_k": 0})).status_code == 422
    assert (await client.post("/rag/query", json={**PERGUNTA, "top_k": 99})).status_code == 422
