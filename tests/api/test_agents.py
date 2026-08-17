"""Testes do endpoint POST /agents/run.

Cobrem o que distingue um grafo de uma sequencia fixa: o caminho muda conforme o plano,
e a falha de um agente nao derruba a execucao.
"""

import json
from collections.abc import Callable
from typing import Any

import pytest
from httpx import AsyncClient

from app.llm.base import LLMProvider
from app.llm.exceptions import LLMTimeoutError
from app.llm.fake_provider import FakeLLMProvider
from tests.conftest import research_json, triage_json

CHAMADOS = [
    {"id": 101, "titulo": "Falha de login no portal", "severidade": "alta"},
    {"id": 102, "titulo": "Falha de login no portal", "severidade": "alta"},
    {"id": 103, "titulo": "Lentidao no relatorio mensal", "severidade": "media"},
]

TAREFA = {"task": "Analise os chamados criticos de hoje e gere um relatorio."}


def analysis_json(**overrides: Any) -> str:
    base: dict[str, Any] = {
        "summary": "Dois chamados repetem falha de login no portal.",
        "findings": [
            {
                "pattern": "Falha de login no portal",
                "occurrences": 2,
                "severity": "alta",
                "evidence": ["Falha de login no portal"],
            }
        ],
        "indicators": [{"name": "chamados_analisados", "value": 3, "unit": "chamados"}],
        "confidence": 0.85,
    }
    return json.dumps({**base, **overrides}, ensure_ascii=False)


def report_json(**overrides: Any) -> str:
    base: dict[str, Any] = {
        "executive_summary": "Falhas de login concentram os chamados criticos do dia.",
        "key_points": ["2 de 3 chamados sao falha de login"],
        "recommendations": [
            {
                "action": "Investigar o servico de autenticacao do portal",
                "rationale": "Duas ocorrencias identicas no mesmo dia",
                "priority": "alta",
            }
        ],
        "limitations": [],
        "confidence": 0.8,
    }
    return json.dumps({**base, **overrides}, ensure_ascii=False)


def roteiro(*agentes: str, com_falha: Exception | None = None) -> list[Any]:
    """Monta a sequencia de respostas do LLM para um plano especifico."""
    passos: list[Any] = [triage_json(suggested_agents=list(agentes), requires_approval=False)]
    for agente in agentes:
        if agente == "research":
            passos.append(com_falha or research_json(citations=[1]))
        elif agente == "analysis":
            passos.append(com_falha or analysis_json())
    passos.append(report_json())
    return passos


# ---------------------------------------------------------------- caminho pelo plano


async def test_plan_with_analysis_only(
    make_client: Callable[[LLMProvider], AsyncClient],
) -> None:
    client = make_client(FakeLLMProvider(script=roteiro("analysis"), repeat_last=False))

    response = await client.post("/agents/run", json={**TAREFA, "data": CHAMADOS})

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "completed"
    assert body["agents_executed"] == ["orchestrator", "analysis", "reporter"]
    assert body["research"] is None


async def test_plan_with_research_and_analysis(
    make_client: Callable[[LLMProvider], AsyncClient],
) -> None:
    """O caminho muda com o plano: mesmo grafo, outra rota."""
    client = make_client(FakeLLMProvider(script=roteiro("research", "analysis"), repeat_last=False))
    await client.post(
        "/documents/upload",
        files={"file": ("politica.txt", b"Politica de chamados criticos do portal.", "text/plain")},
    )

    body = (await client.post("/agents/run", json={**TAREFA, "data": CHAMADOS})).json()

    assert body["agents_executed"] == ["orchestrator", "research", "analysis", "reporter"]


async def test_plan_order_is_followed(
    make_client: Callable[[LLMProvider], AsyncClient],
) -> None:
    client = make_client(FakeLLMProvider(script=roteiro("analysis", "research"), repeat_last=False))
    await client.post(
        "/documents/upload",
        files={"file": ("a.txt", b"Chamados criticos do portal de autenticacao.", "text/plain")},
    )

    body = (await client.post("/agents/run", json={**TAREFA, "data": CHAMADOS})).json()

    assert body["agents_executed"].index("analysis") < body["agents_executed"].index("research")


async def test_read_only_plan_runs_without_asking_anyone(
    make_client: Callable[[LLMProvider], AsyncClient],
) -> None:
    """Nenhum plano de leitura deve encostar no fluxo de aprovacao.

    O caminho de escrita -- pausa, pendencia, retomada -- vive em `test_approvals.py`.
    Aqui o que se protege e o oposto: que ele NAO apareca onde nao deve.
    """
    client = make_client(FakeLLMProvider(script=roteiro("analysis"), repeat_last=False))

    body = (await client.post("/agents/run", json={**TAREFA, "data": CHAMADOS})).json()

    assert body["status"] == "completed"
    assert body["pending_approval"] is None
    assert body["automation"] is None


# ---------------------------------------------------------------- resultado


async def test_returns_the_consolidated_report(
    make_client: Callable[[LLMProvider], AsyncClient],
) -> None:
    client = make_client(FakeLLMProvider(script=roteiro("analysis"), repeat_last=False))

    body = (await client.post("/agents/run", json={**TAREFA, "data": CHAMADOS})).json()

    assert body["report"]["executive_summary"]
    assert body["report"]["recommendations"][0]["priority"] == "alta"
    assert body["analysis"]["findings"][0]["occurrences"] == 2


async def test_records_every_agent_step(
    make_client: Callable[[LLMProvider], AsyncClient],
) -> None:
    client = make_client(FakeLLMProvider(script=roteiro("analysis"), repeat_last=False))

    body = (await client.post("/agents/run", json={**TAREFA, "data": CHAMADOS})).json()

    assert [passo["agent"] for passo in body["steps"]] == [
        "orchestrator",
        "analysis",
        "reporter",
    ]
    assert [passo["sequence"] for passo in body["steps"]] == [1, 2, 3]
    assert body["usage"]["total_tokens"] > 0


async def test_execution_is_retrievable_afterwards(
    make_client: Callable[[LLMProvider], AsyncClient],
) -> None:
    client = make_client(FakeLLMProvider(script=roteiro("analysis"), repeat_last=False))
    execution_id = (await client.post("/agents/run", json={**TAREFA, "data": CHAMADOS})).json()[
        "execution_id"
    ]

    detalhe = (await client.get(f"/executions/{execution_id}")).json()

    assert detalhe["status"] == "completed"
    assert len(detalhe["steps"]) == 3


# ---------------------------------------------------------------- degradacao


async def test_agent_failure_does_not_abort_the_workflow(
    make_client: Callable[[LLMProvider], AsyncClient],
) -> None:
    """Um relatorio que diz "a analise falhou" vale mais que 502 sem nada aproveitado."""
    script = [
        triage_json(suggested_agents=["analysis"], requires_approval=False),
        LLMTimeoutError(),
        report_json(limitations=["A analise falhou por timeout do provedor."]),
    ]
    client = make_client(FakeLLMProvider(script=script, repeat_last=False))

    response = await client.post("/agents/run", json={**TAREFA, "data": CHAMADOS})

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "completed"
    assert body["analysis"] is None
    assert body["errors"][0]["agent"] == "analysis"
    assert body["errors"][0]["code"] == "llm_timeout"
    assert body["report"] is not None


async def test_failed_step_is_recorded_in_the_chain(
    make_client: Callable[[LLMProvider], AsyncClient],
) -> None:
    script = [
        triage_json(suggested_agents=["analysis"], requires_approval=False),
        LLMTimeoutError(),
        report_json(limitations=["A analise falhou."]),
    ]
    client = make_client(FakeLLMProvider(script=script, repeat_last=False))

    body = (await client.post("/agents/run", json={**TAREFA, "data": CHAMADOS})).json()

    falha = next(passo for passo in body["steps"] if passo["agent"] == "analysis")
    assert falha["status"] == "failed"
    assert falha["error_code"] == "llm_timeout"


async def test_planning_failure_ends_the_execution(
    make_client: Callable[[LLMProvider], AsyncClient],
) -> None:
    """Sem plano nao ha o que executar: esta e a unica falha fatal do grafo."""
    client = make_client(FakeLLMProvider(script=[LLMTimeoutError()]))

    body = (await client.post("/agents/run", json=TAREFA)).json()

    assert body["status"] == "failed"
    assert body["report"] is None
    assert body["errors"][0]["agent"] == "orchestrator"


async def test_analysis_without_data_is_skipped_with_a_reason(
    make_client: Callable[[LLMProvider], AsyncClient],
) -> None:
    """Agente que roda sem ter o que fazer precisa aparecer no rastro, nao sumir."""
    script = [
        triage_json(suggested_agents=["analysis"], requires_approval=False),
        report_json(limitations=["Nenhum dado foi fornecido para analise."]),
    ]
    client = make_client(FakeLLMProvider(script=script, repeat_last=False))

    body = (await client.post("/agents/run", json=TAREFA)).json()

    assert body["analysis"]["skipped"] is True
    passo = next(p for p in body["steps"] if p["agent"] == "analysis")
    assert passo["output"]["skipped"] is True


async def test_research_without_knowledge_base_is_reported(
    make_client: Callable[[LLMProvider], AsyncClient],
) -> None:
    """Base sem cobertura nao e erro: e informacao para o relatorio."""
    script = [
        triage_json(suggested_agents=["research"], requires_approval=False),
        report_json(limitations=["A base de conhecimento nao cobre o assunto."]),
    ]
    client = make_client(FakeLLMProvider(script=script, repeat_last=False))

    body = (await client.post("/agents/run", json=TAREFA)).json()

    assert body["research"]["answered"] is False
    assert body["status"] == "completed"


# ---------------------------------------------------------------- validacao


@pytest.mark.parametrize("payload", [{}, {"task": ""}, {"task": "ab"}, {"task": "x" * 8_001}])
async def test_rejects_invalid_payloads(client: AsyncClient, payload: dict[str, str]) -> None:
    response = await client.post("/agents/run", json=payload)

    assert response.status_code == 422
