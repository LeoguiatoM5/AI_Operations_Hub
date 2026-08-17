"""Testes do fluxo de aprovacao humana.

A garantia central do V4, e a unica que nao pode falhar em silencio: **nenhuma acao de
escrita acontece sem alguem autorizar**. Quase todo teste aqui checa isso por dois lados
-- o que a API respondeu E o que chegou (ou nao chegou) no canal de destino. Verificar so
o corpo da resposta deixaria passar exatamente o defeito mais grave: a API dizer que
esta esperando enquanto a mensagem ja foi enviada.
"""

import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.integrations.callback import MemoryPublisher
from app.llm.base import LLMProvider
from app.llm.exceptions import LLMTimeoutError
from app.llm.fake_provider import FakeLLMProvider
from app.main import create_app
from app.rag.embeddings import FakeEmbeddingProvider
from app.rag.memory_store import InMemoryVectorStore
from app.tools.notify import Delivery, MemoryNotifier
from tests.conftest import triage_json

TAREFA = {"task": "Avise o time de operacoes sobre os chamados criticos de hoje."}


class CanalQuebrado:
    """Notificador cujo destino esta fora do ar.

    Implementa o mesmo Protocol `Notifier`, e levanta na entrega. E assim que se testa
    "a ferramenta falhou" sem depender de rede nem de sorte.
    """

    @property
    def name(self) -> str:
        return "quebrado"

    async def send(self, *, title: str, body: str, channel: str) -> Delivery:
        raise RuntimeError("o canal esta fora do ar")


@pytest.fixture
async def client_com_canal_quebrado(
    settings: Settings, engine: AsyncEngine
) -> AsyncIterator[AsyncClient]:
    app = create_app(
        settings,
        engine=engine,
        llm_provider=FakeLLMProvider(script=roteiro_de_escrita(), repeat_last=False),
        embedding_provider=FakeEmbeddingProvider(),
        vector_store=InMemoryVectorStore(),
        checkpointer=MemorySaver(),
        notifier=CanalQuebrado(),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


def report_json(**overrides: Any) -> str:
    base: dict[str, Any] = {
        "executive_summary": "O time foi avisado sobre os chamados criticos.",
        "key_points": ["Notificacao enviada ao canal de operacoes"],
        "recommendations": [],
        "limitations": [],
        "confidence": 0.8,
    }
    return json.dumps({**base, **overrides}, ensure_ascii=False)


def tool_call_json(**overrides: Any) -> str:
    base: dict[str, Any] = {
        "tool": "send_notification",
        "arguments": {
            "title": "Chamados criticos",
            "body": "Ha tres chamados criticos em aberto hoje.",
            "channel": "operacoes",
        },
        "reason": "O pedido e avisar o time de operacoes.",
    }
    return json.dumps({**base, **overrides}, ensure_ascii=False)


def roteiro_de_escrita(*, chamada: str | None = None) -> list[Any]:
    """Plano que leva a uma acao de escrita: triagem -> automacao -> relatorio."""
    return [
        triage_json(suggested_agents=["automation"], requires_approval=True),
        chamada or tool_call_json(),
        report_json(),
    ]


async def pausar(
    make_client: Callable[[LLMProvider], AsyncClient], roteiro: list[Any] | None = None
) -> tuple[AsyncClient, dict[str, Any]]:
    """Executa ate a pausa e devolve o cliente e o corpo da resposta."""
    client = make_client(FakeLLMProvider(script=roteiro or roteiro_de_escrita(), repeat_last=False))
    body = (await client.post("/agents/run", json=TAREFA)).json()
    return client, body


# ---------------------------------------------------------------- a pausa


async def test_write_action_stops_before_executing(
    make_client: Callable[[LLMProvider], AsyncClient], notifier: MemoryNotifier
) -> None:
    """O teste que define o V4: a acao foi planejada, e NAO aconteceu."""
    _client, body = await pausar(make_client)

    assert body["status"] == "waiting_approval"
    assert body["pending_approval"]["tool"] == "send_notification"
    assert notifier.messages == [], "a mensagem nao pode ter sido enviada antes da decisao"


async def test_the_pending_action_shows_exactly_what_will_run(
    make_client: Callable[[LLMProvider], AsyncClient],
) -> None:
    """Quem aprova precisa ver os argumentos reais, nao um resumo deles."""
    _client, body = await pausar(make_client)

    pendencia = body["pending_approval"]
    assert pendencia["arguments"]["channel"] == "operacoes"
    assert pendencia["arguments"]["title"] == "Chamados criticos"
    assert pendencia["reason"]


async def test_the_execution_is_not_finished_while_it_waits(
    make_client: Callable[[LLMProvider], AsyncClient],
) -> None:
    """`waiting_approval` nao e um estado final: a execucao continua viva."""
    client, body = await pausar(make_client)

    detalhe = (await client.get(f"/executions/{body['execution_id']}")).json()

    assert detalhe["status"] == "waiting_approval"
    assert detalhe["duration_ms"] is None, "duracao carimbada indicaria execucao encerrada"


async def test_the_report_is_not_written_before_the_decision(
    make_client: Callable[[LLMProvider], AsyncClient],
) -> None:
    """Relatar antes de decidir seria relatar uma acao que talvez nunca aconteca."""
    _client, body = await pausar(make_client)

    assert body["report"] is None


async def test_the_pending_action_appears_in_the_queue(
    make_client: Callable[[LLMProvider], AsyncClient],
) -> None:
    client, body = await pausar(make_client)

    fila = (await client.get("/approvals", params={"status": "pending"})).json()

    assert fila["total"] == 1
    assert fila["items"][0]["id"] == body["pending_approval"]["id"]
    assert fila["items"][0]["status"] == "pending"


# ---------------------------------------------------------------- aprovacao


async def test_approving_executes_the_action(
    make_client: Callable[[LLMProvider], AsyncClient], notifier: MemoryNotifier
) -> None:
    client, body = await pausar(make_client)

    response = await client.post(
        f"/approvals/{body['pending_approval']['id']}/approve",
        json={"decided_by": "leonardo", "reason": "Procede."},
    )

    assert response.status_code == 200
    assert notifier.messages[0].channel == "operacoes"
    assert notifier.messages[0].title == "Chamados criticos"


async def test_approving_finishes_the_execution(
    make_client: Callable[[LLMProvider], AsyncClient],
) -> None:
    client, body = await pausar(make_client)

    depois = (
        await client.post(
            f"/approvals/{body['pending_approval']['id']}/approve",
            json={"decided_by": "leonardo"},
        )
    ).json()

    assert depois["status"] == "completed"
    assert depois["automation"]["executed"] is True
    assert depois["report"] is not None, "o relatorio so e escrito depois da acao"


async def test_the_decision_is_recorded_with_its_author(
    make_client: Callable[[LLMProvider], AsyncClient],
) -> None:
    """Autorizacao sem autor nao e auditavel."""
    client, body = await pausar(make_client)
    approval_id = body["pending_approval"]["id"]

    await client.post(
        f"/approvals/{approval_id}/approve",
        json={"decided_by": "leonardo", "reason": "Impacto conhecido."},
    )

    registro = (await client.get(f"/approvals/{approval_id}")).json()
    assert registro["status"] == "approved"
    assert registro["decided_by"] == "leonardo"
    assert registro["decision_reason"] == "Impacto conhecido."
    assert registro["decided_at"] is not None


async def test_resuming_does_not_replan(
    make_client: Callable[[LLMProvider], AsyncClient],
) -> None:
    """A retomada parte do checkpoint: nada do que ja rodou e pago de novo.

    O roteiro do provedor tem exatamente as chamadas necessarias. Se a retomada
    reexecutasse a triagem ou o planejamento da acao, o provedor ficaria sem roteiro e o
    teste quebraria -- que e como esta afirmacao se sustenta.
    """
    provider = FakeLLMProvider(script=roteiro_de_escrita(), repeat_last=False)
    client = make_client(provider)
    body = (await client.post("/agents/run", json=TAREFA)).json()
    chamadas_ate_a_pausa = provider.call_count

    await client.post(
        f"/approvals/{body['pending_approval']['id']}/approve", json={"decided_by": "leonardo"}
    )

    assert chamadas_ate_a_pausa == 2, "triagem + escolha da ferramenta"
    assert provider.call_count == 3, "so o relatorio roda na retomada"


# ---------------------------------------------------------------- recusa


async def test_rejecting_does_not_execute_the_action(
    make_client: Callable[[LLMProvider], AsyncClient], notifier: MemoryNotifier
) -> None:
    client, body = await pausar(make_client)

    await client.post(
        f"/approvals/{body['pending_approval']['id']}/reject",
        json={"decided_by": "leonardo", "reason": "Canal errado."},
    )

    assert notifier.messages == []


async def test_rejecting_still_finishes_the_execution(
    make_client: Callable[[LLMProvider], AsyncClient],
) -> None:
    """Recusa nao e erro: o sistema funcionou como deveria, e o relatorio sai."""
    client, body = await pausar(make_client)

    depois = (
        await client.post(
            f"/approvals/{body['pending_approval']['id']}/reject",
            json={"decided_by": "leonardo", "reason": "Canal errado."},
        )
    ).json()

    assert depois["status"] == "completed"
    assert depois["automation"]["rejected"] is True
    assert depois["automation"]["decided_by"] == "leonardo"
    assert depois["report"] is not None


async def test_a_rejected_action_is_recorded_as_a_step_not_as_a_failure(
    make_client: Callable[[LLMProvider], AsyncClient],
) -> None:
    """Gravar recusa como falha faria um painel de erros acusar problema toda vez que
    alguem dissesse "nao"."""
    client, body = await pausar(make_client)

    depois = (
        await client.post(
            f"/approvals/{body['pending_approval']['id']}/reject", json={"decided_by": "leonardo"}
        )
    ).json()

    passo = next(p for p in depois["steps"] if p["action"] == "execute_tool")
    assert passo["status"] == "completed"
    assert passo["output"]["executed"] is False


# ---------------------------------------------------------------- conflito e erro


async def test_the_same_approval_cannot_be_decided_twice(
    make_client: Callable[[LLMProvider], AsyncClient], notifier: MemoryNotifier
) -> None:
    """Sem isso, dois cliques no botao enviariam a mensagem duas vezes."""
    client, body = await pausar(make_client)
    approval_id = body["pending_approval"]["id"]
    await client.post(f"/approvals/{approval_id}/approve", json={"decided_by": "leonardo"})

    segunda = await client.post(
        f"/approvals/{approval_id}/approve", json={"decided_by": "outra_pessoa"}
    )

    assert segunda.status_code == 409
    assert segunda.json()["error"]["code"] == "approval_already_decided"
    assert len(notifier.messages) == 1, "a acao nao pode ter sido executada duas vezes"


async def test_rejecting_an_approved_action_is_refused(
    make_client: Callable[[LLMProvider], AsyncClient],
) -> None:
    client, body = await pausar(make_client)
    approval_id = body["pending_approval"]["id"]
    await client.post(f"/approvals/{approval_id}/approve", json={"decided_by": "leonardo"})

    resposta = await client.post(
        f"/approvals/{approval_id}/reject", json={"decided_by": "leonardo"}
    )

    assert resposta.status_code == 409


async def test_unknown_approval_returns_404(client: AsyncClient) -> None:
    resposta = await client.post("/approvals/nao-existe/approve", json={"decided_by": "leonardo"})

    assert resposta.status_code == 404


async def test_a_decision_requires_an_author(
    make_client: Callable[[LLMProvider], AsyncClient],
) -> None:
    client, body = await pausar(make_client)

    resposta = await client.post(f"/approvals/{body['pending_approval']['id']}/approve", json={})

    assert resposta.status_code == 422


async def test_a_failed_tool_does_not_erase_the_authorization(
    client_com_canal_quebrado: AsyncClient,
) -> None:
    """A decisao e gravada e confirmada ANTES da retomada.

    Na ordem inversa, a falha da ferramenta levaria embora o registro da autorizacao -- e
    "quem mandou fazer isso?" ficaria sem resposta exatamente no caso em que a pergunta e
    feita.
    """
    client = client_com_canal_quebrado
    body = (await client.post("/agents/run", json=TAREFA)).json()
    approval_id = body["pending_approval"]["id"]

    await client.post(f"/approvals/{approval_id}/approve", json={"decided_by": "leonardo"})

    registro = (await client.get(f"/approvals/{approval_id}")).json()
    assert registro["status"] == "approved"
    assert registro["decided_by"] == "leonardo"


async def test_a_failed_tool_is_reported_without_aborting_the_workflow(
    client_com_canal_quebrado: AsyncClient,
) -> None:
    """Canal fora do ar e falha de infraestrutura, nao motivo para perder a execucao."""
    client = client_com_canal_quebrado
    body = (await client.post("/agents/run", json=TAREFA)).json()

    depois = (
        await client.post(
            f"/approvals/{body['pending_approval']['id']}/approve",
            json={"decided_by": "leonardo"},
        )
    ).json()

    assert depois["status"] == "completed"
    assert depois["automation"] is None
    assert depois["errors"][0]["code"] == "tool_execution_failed"
    assert depois["report"] is not None


# ---------------------------------------------------------------- callback de resultado


async def test_the_callback_fires_only_after_the_human_decision(
    make_client: Callable[[LLMProvider], AsyncClient], publisher: MemoryPublisher
) -> None:
    """Quem disparou a execucao recebeu `waiting_approval` e foi embora. O resultado de
    verdade so existe depois da decisao -- e e ai que alguem precisa ser avisado."""
    client, body = await pausar(make_client)

    assert publisher.published == [], "nao ha resultado a publicar enquanto se espera"

    await client.post(
        f"/approvals/{body['pending_approval']['id']}/approve", json={"decided_by": "leonardo"}
    )

    assert len(publisher.published) == 1
    assert publisher.published[0]["status"] == "completed"
    assert publisher.published[0]["automation"]["executed"] is True


async def test_a_rejection_is_also_published(
    make_client: Callable[[LLMProvider], AsyncClient], publisher: MemoryPublisher
) -> None:
    """Recusa e um desfecho, nao um silencio: quem disparou precisa saber."""
    client, body = await pausar(make_client)

    await client.post(
        f"/approvals/{body['pending_approval']['id']}/reject",
        json={"decided_by": "leonardo", "reason": "Canal errado."},
    )

    assert publisher.published[0]["automation"]["rejected"] is True


async def test_a_synchronous_run_publishes_nothing(
    make_client: Callable[[LLMProvider], AsyncClient], publisher: MemoryPublisher
) -> None:
    """O resultado ja saiu na resposta HTTP. Publicar tambem entregaria a mesma
    informacao duas vezes ao consumidor."""
    roteiro = [
        triage_json(suggested_agents=["automation"], requires_approval=True),
        tool_call_json(
            tool="search_knowledge_base",
            arguments={"query": "chamados criticos do portal"},
            reason="Conferir a base antes de agir.",
        ),
        report_json(),
    ]
    client = make_client(FakeLLMProvider(script=roteiro, repeat_last=False))

    body = (await client.post("/agents/run", json=TAREFA)).json()

    assert body["status"] == "completed"
    assert publisher.published == []


async def test_the_published_body_is_the_same_as_the_api_response(
    make_client: Callable[[LLMProvider], AsyncClient], publisher: MemoryPublisher
) -> None:
    """Um formato so. O consumidor nao deve precisar de dois parsers conforme a execucao
    tenha terminado na resposta HTTP ou horas depois."""
    client, body = await pausar(make_client)

    resposta = (
        await client.post(
            f"/approvals/{body['pending_approval']['id']}/approve",
            json={"decided_by": "leonardo"},
        )
    ).json()

    assert publisher.published[0] == resposta


# ---------------------------------------------------------------- leitura nao pausa


async def test_a_read_action_runs_without_approval(
    make_client: Callable[[LLMProvider], AsyncClient],
) -> None:
    """A regra tem que valer nos dois sentidos: exigir aprovacao para leitura tornaria o
    sistema inutilizavel, e nenhum teste de escrita acusaria isso."""
    roteiro = [
        triage_json(suggested_agents=["automation"], requires_approval=True),
        tool_call_json(
            tool="search_knowledge_base",
            arguments={"query": "chamados criticos do portal"},
            reason="Preciso conferir o que a base diz.",
        ),
        report_json(),
    ]
    client = make_client(FakeLLMProvider(script=roteiro, repeat_last=False))

    body = (await client.post("/agents/run", json=TAREFA)).json()

    assert body["status"] == "completed"
    assert body["pending_approval"] is None
    assert body["automation"]["executed"] is True


# ---------------------------------------------------------------- degradacao


async def test_a_failure_choosing_the_tool_does_not_abort_the_workflow(
    make_client: Callable[[LLMProvider], AsyncClient],
) -> None:
    """Mesma regra dos outros agentes: o fluxo entrega o que conseguiu."""
    roteiro = [
        triage_json(suggested_agents=["automation"], requires_approval=True),
        LLMTimeoutError(),
        report_json(limitations=["Nao foi possivel decidir a acao a executar."]),
    ]
    client = make_client(FakeLLMProvider(script=roteiro, repeat_last=False))

    body = (await client.post("/agents/run", json=TAREFA)).json()

    assert body["status"] == "completed"
    assert body["pending_approval"] is None
    assert body["errors"][0]["agent"] == "automation"
    assert body["report"] is not None
