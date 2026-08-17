"""Teste de contrato dos notificadores.

A mesma bateria roda contra as duas implementacoes de `Notifier`. E o que prova que o
Protocol e uma abstracao de verdade -- e nao apenas uma classe base que a segunda
implementacao contrariou em silencio.

Vale mais aqui do que nos bancos vetoriais: se o `MemoryNotifier` e o `SlackNotifier`
divergirem, o fluxo de aprovacao passa nos testes com o notificador de memoria e falha em
producao com o do Slack -- exatamente no ponto do sistema em que uma falha e mais cara,
porque uma pessoa ja autorizou a acao.
"""

from collections.abc import AsyncIterator

import httpx
import pytest

from app.tools.exceptions import ToolExecutionError
from app.tools.notify import MemoryNotifier, Notifier, NotifyTool
from app.tools.slack import SlackNotifier

WEBHOOK = "https://hooks.slack.com/services/T000/B000/token"


def _slack_que_aceita() -> SlackNotifier:
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, text="ok"))
    return SlackNotifier(
        webhook_url=WEBHOOK,
        destination="#operacoes",
        client=httpx.AsyncClient(transport=transport),
    )


def _slack_que_recusa() -> SlackNotifier:
    transport = httpx.MockTransport(lambda _request: httpx.Response(403, text="invalid_token"))
    return SlackNotifier(
        webhook_url=WEBHOOK,
        destination="#operacoes",
        client=httpx.AsyncClient(transport=transport),
    )


@pytest.fixture(params=["memory", "slack"])
async def notifier(request: pytest.FixtureRequest) -> AsyncIterator[Notifier]:
    instancia: Notifier = MemoryNotifier() if request.param == "memory" else _slack_que_aceita()
    yield instancia
    await instancia.aclose()


# ---------------------------------------------------------------- contrato


async def test_satisfies_the_protocol(notifier: Notifier) -> None:
    assert isinstance(notifier, Notifier)


async def test_has_a_name(notifier: Notifier) -> None:
    assert notifier.name


async def test_delivering_returns_a_receipt(notifier: Notifier) -> None:
    entrega = await notifier.send(title="Alerta", body="Corpo.", channel="#operacoes")

    assert entrega.channel
    assert entrega.reference
    assert entrega.delivered_at.tzinfo is not None, "instante sem fuso nao e comparavel"


async def test_the_receipt_identifies_each_delivery(notifier: Notifier) -> None:
    primeira = await notifier.send(title="A", body="Corpo.", channel="#operacoes")
    segunda = await notifier.send(title="B", body="Corpo.", channel="#operacoes")

    assert primeira.reference != segunda.reference


async def test_closing_twice_is_safe(notifier: Notifier) -> None:
    """O encerramento da aplicacao pode acontecer depois de uma falha parcial."""
    await notifier.aclose()
    await notifier.aclose()


async def test_the_tool_works_with_any_notifier(notifier: Notifier) -> None:
    """O contrato que importa de verdade: a ferramenta nao conhece a implementacao."""
    ferramenta = NotifyTool(notifier)

    resultado = await ferramenta.run(
        ferramenta.input_model(title="Alerta", body="Corpo.", channel="#operacoes")
    )

    assert resultado.output["reference"]
    assert resultado.summary


# ---------------------------------------------------------------- falha


@pytest.fixture(params=["memory", "slack"])
def notificador_que_falha(request: pytest.FixtureRequest) -> Notifier:
    if request.param == "slack":
        return _slack_que_recusa()

    class MemoriaIndisponivel(MemoryNotifier):
        async def send(self, *, title: str, body: str, channel: str) -> object:  # type: ignore[override]
            raise ToolExecutionError("canal indisponivel")

    return MemoriaIndisponivel()


async def test_a_failed_delivery_raises_instead_of_returning(
    notificador_que_falha: Notifier,
) -> None:
    """Notificador que engole erro transforma "a mensagem nao chegou" em "tudo certo"."""
    with pytest.raises(ToolExecutionError):
        await notificador_que_falha.send(title="Alerta", body="Corpo.", channel="#operacoes")
