"""Testes do portao de qualidade dentro do workflow.

Aqui o motor deixa de ser uma biblioteca e passa a decidir o desfecho de uma execucao
real: a nota e gravada, o relatorio reprovado volta para correcao, e a execucao que
reprova duas vezes termina em `needs_human_review`.

O roteiro do `FakeLLMProvider` e a ferramenta central: como cada dimensao e uma chamada,
da para escrever exatamente o que cada juiz responde e observar o efeito no desfecho --
sem rede e sem custo.
"""

import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.llm.base import LLMProvider
from app.llm.fake_provider import FakeLLMProvider
from app.main import create_app
from app.rag.embeddings import FakeEmbeddingProvider
from app.rag.memory_store import InMemoryVectorStore
from tests.conftest import triage_json

TAREFA = {"task": "Analise os chamados criticos de hoje e gere um relatorio."}

CHAMADOS = [{"id": 1, "titulo": "Falha de login", "severidade": "alta"}]


def report_json(**overrides: Any) -> str:
    base: dict[str, Any] = {
        "executive_summary": "Falhas de login concentram os chamados.",
        "key_points": ["Dois chamados sao falha de login"],
        "recommendations": [],
        "limitations": [],
        "confidence": 0.8,
    }
    return json.dumps({**base, **overrides}, ensure_ascii=False)


def analysis_json() -> str:
    return json.dumps(
        {
            "summary": "Padrao de falha de login.",
            "findings": [
                {
                    "pattern": "Falha de login",
                    "occurrences": 2,
                    "severity": "alta",
                    "evidence": ["Falha de login"],
                }
            ],
            "indicators": [],
            "confidence": 0.85,
        },
        ensure_ascii=False,
    )


# --- respostas dos juizes, na ordem em que consomem o roteiro ------------------
#
# **Sao TRES, e nao quatro.** Esta tarefa analisa dados fornecidos pelo usuario: nao houve
# pesquisa, entao `source_based` e falso e `grounding` se declara inaplicavel pelo atalho,
# sem gastar chamada. O teste so passa se o atalho de fato disparar -- e por isso ele
# tambem serve de prova de que a economia acontece no fluxo real, e nao so no unitario.
#
# A ordem importa porque o `FakeLLMProvider` e um recurso compartilhado consumido em
# sequencia. `asyncio.gather` garante a ordem dos RESULTADOS, nao a ordem em que as
# corrotinas tocam esse recurso -- aqui elas coincidem porque nenhuma dimensao suspende
# antes de chamar o provider, mas depender disso seria fragil se uma delas passasse a
# fazer I/O antes.


def juizes(*, bom: bool) -> list[str]:
    return [
        json.dumps({"addresses_request": bom, "off_topic": [], "reason": "ok" if bom else "fora"}),
        json.dumps({"items": [{"item": "analisar", "covered": bom, "note": ""}]}),
        json.dumps({"contradictions": []}),
    ]


@pytest.fixture
def quality_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"quality_enabled": True, "quality_threshold": 0.7})


@pytest.fixture
async def make_quality_client(
    quality_settings: Settings, engine: AsyncEngine
) -> AsyncIterator[Callable[[LLMProvider], AsyncClient]]:
    criados: list[AsyncClient] = []

    def build(provider: LLMProvider) -> AsyncClient:
        app = create_app(
            quality_settings,
            engine=engine,
            llm_provider=provider,
            embedding_provider=FakeEmbeddingProvider(),
            vector_store=InMemoryVectorStore(),
            checkpointer=MemorySaver(),
        )
        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
        criados.append(client)
        return client

    yield build

    for client in criados:
        await client.aclose()


# ---------------------------------------------------------------- desligado


async def test_disabled_by_default_nothing_is_measured(
    make_client: Callable[[LLMProvider], AsyncClient],
) -> None:
    """`quality: null` significa que ninguem mediu -- diferente de medir e aprovar."""
    roteiro = [
        triage_json(suggested_agents=["analysis"], requires_approval=False),
        analysis_json(),
        report_json(),
    ]
    client = make_client(FakeLLMProvider(script=roteiro, repeat_last=False))

    body = (await client.post("/agents/run", json={**TAREFA, "data": CHAMADOS})).json()

    assert body["status"] == "completed"
    assert body["quality"] is None


async def test_disabled_costs_no_extra_calls(
    make_client: Callable[[LLMProvider], AsyncClient],
) -> None:
    """O argumento para o padrao ser desligado: o portao custa quatro chamadas."""
    provider = FakeLLMProvider(
        script=[
            triage_json(suggested_agents=["analysis"], requires_approval=False),
            analysis_json(),
            report_json(),
        ],
        repeat_last=False,
    )
    client = make_client(provider)

    await client.post("/agents/run", json={**TAREFA, "data": CHAMADOS})

    assert provider.call_count == 3


# ---------------------------------------------------------------- aprovado


async def test_an_approved_report_records_the_score(
    make_quality_client: Callable[[LLMProvider], AsyncClient],
) -> None:
    roteiro = [
        triage_json(suggested_agents=["analysis"], requires_approval=False),
        analysis_json(),
        report_json(),
        *juizes(bom=True),
    ]
    client = make_quality_client(FakeLLMProvider(script=roteiro, repeat_last=False))

    body = (await client.post("/agents/run", json={**TAREFA, "data": CHAMADOS})).json()

    assert body["status"] == "completed"
    assert body["quality"]["passed"] is True
    assert body["quality"]["score"] > 0


async def test_the_score_survives_in_the_execution(
    make_quality_client: Callable[[LLMProvider], AsyncClient],
) -> None:
    """A coluna `quality_score` existe desde o V1 esperando por isto."""
    roteiro = [
        triage_json(suggested_agents=["analysis"], requires_approval=False),
        analysis_json(),
        report_json(),
        *juizes(bom=True),
    ]
    client = make_quality_client(FakeLLMProvider(script=roteiro, repeat_last=False))
    body = (await client.post("/agents/run", json={**TAREFA, "data": CHAMADOS})).json()

    detalhe = (await client.get(f"/executions/{body['execution_id']}")).json()

    assert detalhe["quality_score"] is not None


async def test_the_gate_is_recorded_as_a_step(
    make_quality_client: Callable[[LLMProvider], AsyncClient],
) -> None:
    """O custo de medir precisa aparecer na cadeia, como qualquer outro passo."""
    roteiro = [
        triage_json(suggested_agents=["analysis"], requires_approval=False),
        analysis_json(),
        report_json(),
        *juizes(bom=True),
    ]
    client = make_quality_client(FakeLLMProvider(script=roteiro, repeat_last=False))

    body = (await client.post("/agents/run", json={**TAREFA, "data": CHAMADOS})).json()

    passo = next(p for p in body["steps"] if p["agent"] == "quality")
    assert passo["action"] == "evaluate"
    assert passo["output"]["score"] == body["quality"]["score"]


# ---------------------------------------------------------------- reprovado


async def test_a_rejected_report_is_rewritten_with_the_reason(
    make_quality_client: Callable[[LLMProvider], AsyncClient],
) -> None:
    """O retry dirigido do ED-023, um nivel acima: o modelo recebe o que reprovou."""
    provider = FakeLLMProvider(
        script=[
            triage_json(suggested_agents=["analysis"], requires_approval=False),
            analysis_json(),
            report_json(),
            *juizes(bom=False),
            report_json(executive_summary="Versao corrigida."),
            *juizes(bom=True),
        ],
        repeat_last=False,
    )
    client = make_quality_client(provider)

    body = (await client.post("/agents/run", json={**TAREFA, "data": CHAMADOS})).json()

    assert body["status"] == "completed"
    assert body["report"]["executive_summary"] == "Versao corrigida."
    # O prompt da segunda escrita carrega o motivo da reprovacao.
    # Indice 6: triagem(0), analise(1), relatorio(2), tres juizes(3,4,5).
    segunda_escrita = provider.calls[6][0].content
    assert "reprovacao_da_versao_anterior" in segunda_escrita
    assert "relevance" in segunda_escrita, "o modelo precisa saber QUAL dimensao reprovou"


async def test_failing_twice_ends_in_human_review(
    make_quality_client: Callable[[LLMProvider], AsyncClient],
) -> None:
    """Reter a resposta seria pior: um estado proprio permite procurar por esses casos."""
    provider = FakeLLMProvider(
        script=[
            triage_json(suggested_agents=["analysis"], requires_approval=False),
            analysis_json(),
            report_json(),
            *juizes(bom=False),
            report_json(executive_summary="Segunda tentativa."),
            *juizes(bom=False),
        ],
        repeat_last=False,
    )
    client = make_quality_client(provider)

    body = (await client.post("/agents/run", json={**TAREFA, "data": CHAMADOS})).json()

    assert body["status"] == "needs_human_review"
    assert body["quality"]["passed"] is False
    assert body["report"] is not None, "a resposta e entregue mesmo reprovada"


async def test_the_loop_does_not_run_forever(
    make_quality_client: Callable[[LLMProvider], AsyncClient],
) -> None:
    """O roteiro tem exatamente duas rodadas. Uma terceira estouraria o provider."""
    provider = FakeLLMProvider(
        script=[
            triage_json(suggested_agents=["analysis"], requires_approval=False),
            analysis_json(),
            report_json(),
            *juizes(bom=False),
            report_json(),
            *juizes(bom=False),
        ],
        repeat_last=False,
    )
    client = make_quality_client(provider)

    response = await client.post("/agents/run", json={**TAREFA, "data": CHAMADOS})

    assert response.status_code == 201
    assert provider.call_count == 10, "triagem + analise + 2x(relatorio + 3 juizes)"
