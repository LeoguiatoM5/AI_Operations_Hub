"""Testes do callback de resultado.

A regra que este arquivo protege e contraintuitiva e por isso precisa de teste explicito:
**engolir o erro aqui e o comportamento correto**. Quando o callback roda, a acao ja foi
executada e a aprovacao ja esta gravada. Levantar excecao transformaria "o aviso nao
chegou" em "a execucao falhou" -- mentira sobre um trabalho que deu certo.

Compare com `test_slack_notifier.py`, onde a mesma falha de rede DEVE virar excecao: la a
acao aprovada nao aconteceu, e o silencio seria o pior desfecho possivel.
"""

import httpx
import pytest

from app.core.config import Settings
from app.integrations.callback import (
    MemoryPublisher,
    NullPublisher,
    WebhookPublisher,
    build_result_publisher,
)

URL = "http://localhost:5678/webhook/resultado"

PAYLOAD = {"execution_id": "abc123", "status": "completed", "task": "Avise o time."}


def publicador(
    *,
    status: int = 200,
    erro: Exception | None = None,
    registro: list[httpx.Request] | None = None,
) -> WebhookPublisher:
    def responder(request: httpx.Request) -> httpx.Response:
        if registro is not None:
            registro.append(request)
        if erro is not None:
            raise erro
        return httpx.Response(status, text="")

    return WebhookPublisher(
        url=URL, client=httpx.AsyncClient(transport=httpx.MockTransport(responder))
    )


@pytest.fixture
def requisicoes() -> list[httpx.Request]:
    return []


# ---------------------------------------------------------------- entrega


async def test_publishes_the_payload(requisicoes: list[httpx.Request]) -> None:
    import json

    entregue = await publicador(registro=requisicoes).publish(PAYLOAD)

    assert entregue is True
    assert str(requisicoes[0].url) == URL
    assert json.loads(requisicoes[0].content)["execution_id"] == "abc123"


@pytest.mark.parametrize("status", [200, 201, 202, 204])
async def test_any_success_status_counts_as_delivered(status: int) -> None:
    """O n8n responde 200 no webhook padrao, mas pode responder 204 sem corpo."""
    assert await publicador(status=status).publish(PAYLOAD) is True


# ---------------------------------------------------------------- falha nao propaga


@pytest.mark.parametrize("status", [400, 404, 500, 502])
async def test_a_refused_callback_is_reported_not_raised(status: int) -> None:
    assert await publicador(status=status).publish(PAYLOAD) is False


@pytest.mark.parametrize(
    "erro",
    [
        httpx.ConnectError("n8n fora do ar"),
        httpx.ReadTimeout("demorou demais"),
        httpx.RemoteProtocolError("resposta malformada"),
    ],
)
async def test_a_network_failure_never_raises(erro: Exception) -> None:
    """Se o n8n estiver fora do ar, a execucao ja terminou bem -- e continua tendo
    terminado bem."""
    assert await publicador(erro=erro).publish(PAYLOAD) is False


# ---------------------------------------------------------------- construcao


def test_without_a_url_nothing_is_published() -> None:
    settings = Settings(_env_file=None, app_env="test")  # type: ignore[call-arg]

    assert build_result_publisher(settings).name == "none"


def test_the_url_alone_turns_the_callback_on() -> None:
    """Nao ha seletor separado: um `RESULT_CALLBACK=webhook` sem URL seria um estado
    invalido que a configuracao aceitaria e o runtime descobriria tarde demais."""
    settings = Settings(_env_file=None, app_env="test", result_callback_url=URL)  # type: ignore[call-arg]

    assert build_result_publisher(settings).name == "webhook"


async def test_the_default_publisher_does_not_grow_in_memory() -> None:
    """Sem callback configurado o certo e descartar, e nao acumular payloads a espera de
    alguem -- o que vazaria memoria num processo de vida longa."""
    nulo = NullPublisher()

    for _ in range(100):
        assert await nulo.publish(PAYLOAD) is False

    assert not hasattr(nulo, "published")


async def test_the_memory_publisher_records_what_it_received() -> None:
    memoria = MemoryPublisher()

    await memoria.publish(PAYLOAD)

    assert memoria.published[0]["execution_id"] == "abc123"
