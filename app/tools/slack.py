"""Notificador do Slack, via incoming webhook.

A integracao mais barata que existe: um POST com JSON para uma URL. Nao ha SDK, nao ha
OAuth, nao ha fluxo de token -- e por isso ela e a primeira integracao real do projeto.

Tres decisoes moram aqui, e nenhuma delas e sobre HTTP.

**A URL e a credencial.** `https://hooks.slack.com/services/T.../B.../XXXX` nao e um
endereco publico: quem tem a URL posta no canal. Ela entra como `SecretStr`, nao aparece
em log, e nao entra em `details` de erro -- inclusive porque `details` sai na resposta da
API.

**Nao ha retry.** Ver `send`.

**O destino nao e escolhido por nos.** O canal fica gravado no webhook, do lado do Slack.
Fingir que `channel` roteia a mensagem seria mentir no comprovante de uma acao que um
humano autorizou; entao o pedido vira um rotulo dentro do texto, e o comprovante devolve
o destino real.
"""

from datetime import datetime
from uuid import uuid4

import httpx

from app.core.logging import get_logger
from app.db.types import utcnow
from app.tools.exceptions import ToolExecutionError
from app.tools.notify import Delivery

logger = get_logger(__name__)

#: Trecho do corpo de erro que sobe junto da excecao. O Slack responde texto curto
#: ("invalid_token", "no_service"), mas um proxy no caminho pode devolver uma pagina HTML
#: inteira -- e ela nao tem por que chegar ao cliente da nossa API.
MAX_ERROR_BODY = 200


class SlackNotifier:
    """Publica mensagens em um canal do Slack por incoming webhook."""

    def __init__(
        self,
        *,
        webhook_url: str,
        destination: str = "slack",
        timeout_seconds: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """
        Args:
            destination: rotulo do canal configurado no webhook, so para o comprovante.
                O Slack nao informa qual e; quem sabe e quem criou a integracao.
            client: cliente HTTP injetavel. E o que permite testar a traducao de cada
                codigo de resposta sem rede e sem um webhook de verdade.
        """
        self._webhook_url = webhook_url
        self._destination = destination
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)

    @property
    def name(self) -> str:
        return "slack"

    def _format(self, *, title: str, body: str, channel: str) -> str:
        """Monta o texto da mensagem.

        O canal pedido vira uma linha do proprio texto quando difere do destino
        configurado. Assim a informacao nao se perde -- quem le no Slack ve para quem a
        mensagem era destinada -- sem que o sistema finja ter roteado nada.
        """
        linhas = [f"*{title}*", body]
        if channel and channel != self._destination:
            linhas.append(f"_(canal solicitado: {channel})_")
        return "\n".join(linhas)

    async def send(self, *, title: str, body: str, channel: str) -> Delivery:
        """Publica a mensagem. Falha alto, e NAO tenta de novo.

        Um incoming webhook nao aceita chave de idempotencia. Um timeout de leitura e
        indistinguivel de "a mensagem chegou e a resposta se perdeu" -- entao repetir tem
        chance real de publicar duas vezes o mesmo aviso num canal que a equipe ja leu.

        Daria para retentar apenas `ConnectError`, que acontece antes de a requisicao
        sair. Nao vale: essa classificacao depende de detalhes do httpx, de proxy e de
        DNS, e uma heuristica fragil governando duplicacao de mensagem e pior que uma
        regra simples. Uma acao de escrita que falha volta como erro para quem aprovou, e
        a pessoa decide se refaz.

        Compare com a camada de LLM, que **tem** retry (ED-011): la a operacao e de
        leitura e repetir so custa tokens. Aqui repetir custa credibilidade.
        """
        payload = {"text": self._format(title=title, body=body, channel=channel)}

        try:
            response = await self._client.post(self._webhook_url, json=payload)
        except httpx.TimeoutException as error:
            raise ToolExecutionError(
                "O Slack nao respondeu a tempo. A mensagem pode ou nao ter sido publicada.",
                details={"notifier": self.name, "destination": self._destination},
            ) from error
        except httpx.HTTPError as error:
            raise ToolExecutionError(
                "Falha de rede ao publicar no Slack.",
                details={"notifier": self.name, "error_type": type(error).__name__},
            ) from error

        if response.status_code != httpx.codes.OK:
            # A URL NAO entra nos detalhes: `details` sai na resposta da nossa API, e
            # publicar a credencial num corpo de erro seria vaza-la para quem provocou o
            # erro.
            raise ToolExecutionError(
                f"O Slack recusou a mensagem (HTTP {response.status_code}).",
                details={
                    "notifier": self.name,
                    "status_code": response.status_code,
                    "slack_response": response.text[:MAX_ERROR_BODY],
                },
            )

        entrega = Delivery(
            channel=self._destination,
            reference=_reference(utcnow()),
            delivered_at=utcnow(),
        )
        logger.info(
            "notification_sent",
            notifier=self.name,
            destination=self._destination,
            requested_channel=channel,
            title=title,
            # A URL do webhook nunca e logada: ela e a credencial.
        )
        return entrega

    async def aclose(self) -> None:
        await self._client.aclose()


def _reference(instante: datetime) -> str:
    """Identificador local da entrega.

    O incoming webhook nao devolve identificador de mensagem, entao este e o unico
    numero que existe para amarrar uma entrega ao seu registro.

    O sufixo aleatorio nao e enfeite: a primeira versao usava so o instante, e o teste de
    contrato (`test_the_receipt_identifies_each_delivery`) reprovou -- dois envios no
    mesmo microssegundo produziam a mesma referencia. Um identificador que colide nao
    identifica nada.
    """
    return f"slack-{instante.strftime('%Y%m%dT%H%M%S')}-{uuid4().hex[:8]}"
