"""A prova de que o human-in-the-loop nao depende do processo que o iniciou.

Uma aprovacao humana pode demorar minutos ou dias. Nesse intervalo o servidor reinicia,
faz deploy, cai. Se a retomada dependesse de algo vivo em memoria, o recurso seria uma
demonstracao de laboratorio: funcionaria no teste e falharia no primeiro `docker restart`.

Por isso este arquivo **derruba a aplicacao inteira** entre a pausa e a decisao. Nada
sobrevive em memoria: nem o app, nem o engine, nem o checkpointer, nem o notificador. A
segunda instancia so tem os dois arquivos em disco -- o banco e os checkpoints -- e a
mensagem precisa sair mesmo assim.

Custa mais caro que o resto da suite (toca disco de verdade) e vale cada milissegundo:
e o unico teste que confronta o `AsyncSqliteSaver` real com uma pausa real (ED-047).
"""

import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.db.session import create_engine, create_schema
from app.llm.fake_provider import FakeLLMProvider
from app.main import create_app
from app.rag.embeddings import FakeEmbeddingProvider
from app.rag.memory_store import InMemoryVectorStore
from app.tools.notify import MemoryNotifier
from app.workflows.checkpointer import create_checkpointer
from tests.conftest import triage_json

TAREFA = {"task": "Avise o time de operacoes sobre os chamados criticos de hoje."}

ACAO = {
    "tool": "send_notification",
    "arguments": {
        "title": "Chamados criticos",
        "body": "Ha tres chamados criticos em aberto.",
        "channel": "operacoes",
    },
    "reason": "O pedido e avisar o time de operacoes.",
}

RELATORIO = {
    "executive_summary": "O time foi avisado.",
    "key_points": ["Notificacao enviada"],
    "recommendations": [],
    "limitations": [],
    "confidence": 0.8,
}

#: Ate a pausa: triagem e escolha da acao. O relatorio nao entra -- o grafo para antes.
ATE_A_PAUSA = [
    triage_json(suggested_agents=["automation"], requires_approval=True),
    json.dumps(ACAO),
]

#: Depois da retomada roda UM passo: o relatorio. O roteiro tem exatamente um item, entao
#: qualquer reexecucao de etapa anterior estoura o provedor e derruba o teste.
DEPOIS_DA_RETOMADA = [json.dumps(RELATORIO)]


@pytest.fixture
def config(temp_dir: Path) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        app_env="test",
        log_level="ERROR",
        # Arquivo, e nao `:memory:`: o banco precisa sobreviver ao fim da instancia.
        database_url=f"sqlite+aiosqlite:///{(temp_dir / 'app.db').as_posix()}",
        checkpoint_path=str(temp_dir / "checkpoints.db"),
        vector_store="memory",
    )


@asynccontextmanager
async def instancia(
    config: Settings, *, roteiro: Sequence[Any], notifier: MemoryNotifier
) -> AsyncIterator[AsyncClient]:
    """Uma instancia completa da aplicacao, encerrada por inteiro na saida.

    Reproduz o que o lifespan faz (schema e checkpointer persistente) sem depender dele:
    o cliente ASGI do httpx nao executa lifespan.
    """
    engine = create_engine(config)
    await create_schema(engine)
    saver, conexao = await create_checkpointer(config)

    app = create_app(
        config,
        engine=engine,
        llm_provider=FakeLLMProvider(script=roteiro, repeat_last=False),
        embedding_provider=FakeEmbeddingProvider(),
        vector_store=InMemoryVectorStore(),
        checkpointer=saver,
        notifier=notifier,
    )

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            yield client
    finally:
        # Ordem importa no Windows: a conexao do checkpointer segura o arquivo.
        await conexao.close()
        await engine.dispose()


async def test_the_action_runs_after_a_full_restart(config: Settings) -> None:
    """O teste que define o V4.2, do inicio ao fim."""
    primeiro_notificador = MemoryNotifier()
    async with instancia(config, roteiro=ATE_A_PAUSA, notifier=primeiro_notificador) as app1:
        pausado = (await app1.post("/agents/run", json=TAREFA)).json()

    assert pausado["status"] == "waiting_approval"
    assert primeiro_notificador.messages == []

    # --- a aplicacao que criou a pendencia nao existe mais ---

    segundo_notificador = MemoryNotifier()
    async with instancia(config, roteiro=DEPOIS_DA_RETOMADA, notifier=segundo_notificador) as app2:
        resposta = await app2.post(
            f"/approvals/{pausado['pending_approval']['id']}/approve",
            json={"decided_by": "leonardo"},
        )

    assert resposta.status_code == 200
    assert resposta.json()["status"] == "completed"
    # A mensagem saiu por um processo que nunca viu a solicitacao original.
    assert segundo_notificador.messages[0].channel == "operacoes"
    assert primeiro_notificador.messages == []


async def test_the_pending_action_is_visible_to_the_new_instance(config: Settings) -> None:
    """Antes de decidir, a nova instancia precisa conseguir MOSTRAR o que esta pendente."""
    async with instancia(config, roteiro=ATE_A_PAUSA, notifier=MemoryNotifier()) as app1:
        pausado = (await app1.post("/agents/run", json=TAREFA)).json()

    async with instancia(config, roteiro=DEPOIS_DA_RETOMADA, notifier=MemoryNotifier()) as app2:
        fila = (await app2.get("/approvals", params={"status": "pending"})).json()

    assert fila["total"] == 1
    assert fila["items"][0]["id"] == pausado["pending_approval"]["id"]
    assert fila["items"][0]["arguments"] == ACAO["arguments"]


async def test_what_runs_is_exactly_what_was_shown(config: Settings) -> None:
    """Nada pode ser reinterpretado entre a tela de aprovacao e a execucao.

    Se a escolha da ferramenta fosse refeita na retomada, o modelo poderia produzir outros
    argumentos -- e a pessoa teria autorizado uma coisa enquanto outra acontecia.
    """
    async with instancia(config, roteiro=ATE_A_PAUSA, notifier=MemoryNotifier()) as app1:
        pausado = (await app1.post("/agents/run", json=TAREFA)).json()
    mostrado = pausado["pending_approval"]["arguments"]

    notificador = MemoryNotifier()
    async with instancia(config, roteiro=DEPOIS_DA_RETOMADA, notifier=notificador) as app2:
        await app2.post(
            f"/approvals/{pausado['pending_approval']['id']}/approve",
            json={"decided_by": "leonardo"},
        )

    enviada = notificador.messages[0]
    assert enviada.title == mostrado["title"]
    assert enviada.body == mostrado["body"]
    assert enviada.channel == mostrado["channel"]


async def test_rejecting_after_a_restart_executes_nothing(config: Settings) -> None:
    async with instancia(config, roteiro=ATE_A_PAUSA, notifier=MemoryNotifier()) as app1:
        pausado = (await app1.post("/agents/run", json=TAREFA)).json()

    notificador = MemoryNotifier()
    async with instancia(config, roteiro=DEPOIS_DA_RETOMADA, notifier=notificador) as app2:
        resposta = await app2.post(
            f"/approvals/{pausado['pending_approval']['id']}/reject",
            json={"decided_by": "leonardo", "reason": "Canal errado."},
        )

    assert resposta.json()["automation"]["rejected"] is True
    assert notificador.messages == []
