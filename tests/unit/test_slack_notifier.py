"""Testes do notificador do Slack.

Nenhum toca a rede: o cliente HTTP e injetado com um `MockTransport`, que responde o que
o teste mandar. E assim que se exercita "o Slack devolveu 403" ou "o Slack nao respondeu"
-- cenarios que um webhook de verdade nao produz sob encomenda.
"""

from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.core.exceptions import ConfigurationError
from app.tools.exceptions import ToolExecutionError
from app.tools.factory import build_notifier
from app.tools.slack import SlackNotifier

WEBHOOK = "https://hooks.slack.com/services/T00000000/B00000000/segredo-que-nao-pode-vazar"


def notificador(
    handler: httpx.MockTransport | None = None,
    *,
    status: int = 200,
    body: str = "ok",
    destination: str = "#operacoes",
    registro: list[httpx.Request] | None = None,
) -> SlackNotifier:
    """Notificador com um Slack simulado."""

    def responder(request: httpx.Request) -> httpx.Response:
        if registro is not None:
            registro.append(request)
        return httpx.Response(status, text=body)

    transport = handler or httpx.MockTransport(responder)
    return SlackNotifier(
        webhook_url=WEBHOOK,
        destination=destination,
        client=httpx.AsyncClient(transport=transport),
    )


def corpo(request: httpx.Request) -> dict[str, Any]:
    import json

    return dict(json.loads(request.content))


# ---------------------------------------------------------------- entrega


async def test_publishes_the_message(notifier_requests: list[httpx.Request]) -> None:
    slack = notificador(registro=notifier_requests)

    entrega = await slack.send(title="Chamados criticos", body="Tres em aberto.", channel="ops")

    assert str(notifier_requests[0].url) == WEBHOOK
    assert "Chamados criticos" in corpo(notifier_requests[0])["text"]
    assert entrega.reference.startswith("slack-")


async def test_the_title_is_highlighted(notifier_requests: list[httpx.Request]) -> None:
    slack = notificador(registro=notifier_requests)

    await slack.send(title="Alerta", body="Corpo da mensagem.", channel="#operacoes")

    assert corpo(notifier_requests[0])["text"].startswith("*Alerta*")


# ---------------------------------------------------------------- o destino nao e nosso


async def test_the_receipt_reports_the_real_destination() -> None:
    """O canal fica gravado no webhook, do lado do Slack. Devolver o canal PEDIDO seria
    mentir no comprovante de uma acao que um humano autorizou."""
    slack = notificador(destination="#operacoes")

    entrega = await slack.send(title="Alerta", body="Corpo.", channel="financeiro")

    assert entrega.channel == "#operacoes"


async def test_the_requested_channel_survives_inside_the_text(
    notifier_requests: list[httpx.Request],
) -> None:
    """A informacao nao se perde: quem le no Slack ve para quem a mensagem era destinada."""
    slack = notificador(destination="#operacoes", registro=notifier_requests)

    await slack.send(title="Alerta", body="Corpo.", channel="financeiro")

    assert "financeiro" in corpo(notifier_requests[0])["text"]


async def test_no_redundant_label_when_the_channel_matches(
    notifier_requests: list[httpx.Request],
) -> None:
    slack = notificador(destination="#operacoes", registro=notifier_requests)

    await slack.send(title="Alerta", body="Corpo.", channel="#operacoes")

    assert "canal solicitado" not in corpo(notifier_requests[0])["text"]


# ---------------------------------------------------------------- falhas


@pytest.mark.parametrize("status", [400, 403, 404, 410, 500])
async def test_a_refused_message_becomes_a_domain_error(status: int) -> None:
    slack = notificador(status=status, body="invalid_token")

    with pytest.raises(ToolExecutionError) as erro:
        await slack.send(title="Alerta", body="Corpo.", channel="ops")

    assert erro.value.details["status_code"] == status
    assert erro.value.http_status == 502


async def test_a_timeout_says_the_outcome_is_unknown() -> None:
    """Dizer "pode ou nao ter sido publicada" e a verdade -- e e o que quem aprovou le."""

    def estourar(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("demorou demais")

    slack = notificador(httpx.MockTransport(estourar))

    with pytest.raises(ToolExecutionError, match="pode ou nao"):
        await slack.send(title="Alerta", body="Corpo.", channel="ops")


async def test_a_network_failure_becomes_a_domain_error() -> None:
    def cair(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sem rota para o host")

    slack = notificador(httpx.MockTransport(cair))

    with pytest.raises(ToolExecutionError) as erro:
        await slack.send(title="Alerta", body="Corpo.", channel="ops")

    assert erro.value.details["error_type"] == "ConnectError"


async def test_a_failure_is_not_retried(notifier_requests: list[httpx.Request]) -> None:
    """Webhook nao aceita chave de idempotencia: repetir pode publicar duas vezes o
    mesmo aviso num canal que a equipe ja leu."""
    slack = notificador(status=500, registro=notifier_requests)

    with pytest.raises(ToolExecutionError):
        await slack.send(title="Alerta", body="Corpo.", channel="ops")

    assert len(notifier_requests) == 1


async def test_a_huge_error_body_is_truncated() -> None:
    """Um proxy no caminho pode devolver uma pagina HTML inteira; ela nao precisa
    atravessar a nossa API."""
    slack = notificador(status=502, body="x" * 5_000)

    with pytest.raises(ToolExecutionError) as erro:
        await slack.send(title="Alerta", body="Corpo.", channel="ops")

    assert len(erro.value.details["slack_response"]) <= 200


async def test_the_webhook_url_never_leaks_in_an_error() -> None:
    """`details` sai no corpo da resposta da API. A URL do webhook E a credencial:
    publica-la ali seria entrega-la a quem provocou o erro."""
    slack = notificador(status=403, body="invalid_token")

    with pytest.raises(ToolExecutionError) as erro:
        await slack.send(title="Alerta", body="Corpo.", channel="ops")

    despejo = f"{erro.value.message} {erro.value.details}"
    assert "segredo-que-nao-pode-vazar" not in despejo
    assert "hooks.slack.com" not in despejo


# ---------------------------------------------------------------- construcao


def test_slack_without_a_webhook_url_fails_on_startup() -> None:
    """Descobrir que falta credencial no instante em que alguem clica "aprovar" seria o
    pior momento possivel: a acao ja foi autorizada."""
    settings = Settings(_env_file=None, app_env="test", notifier="slack")  # type: ignore[call-arg]

    with pytest.raises(ConfigurationError, match="SLACK_WEBHOOK_URL"):
        build_notifier(settings)


async def test_slack_is_built_from_the_configuration() -> None:
    """O caminho de producao da fabrica -- construir de verdade, e nao so recusar."""
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        app_env="test",
        notifier="slack",
        slack_webhook_url=WEBHOOK,
        slack_destination="#operacoes",
    )

    slack = build_notifier(settings)

    assert slack.name == "slack"
    await slack.aclose()


def test_the_default_notifier_needs_no_configuration() -> None:
    settings = Settings(_env_file=None, app_env="test")  # type: ignore[call-arg]

    assert build_notifier(settings).name == "memory"


def test_the_webhook_url_is_hidden_in_the_settings_dump() -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, app_env="test", notifier="slack", slack_webhook_url=WEBHOOK
    )

    assert "segredo-que-nao-pode-vazar" not in repr(settings)
    assert "segredo-que-nao-pode-vazar" not in str(settings.model_dump())


@pytest.fixture
def notifier_requests() -> list[httpx.Request]:
    """Requisicoes capturadas pelo transporte simulado."""
    return []
