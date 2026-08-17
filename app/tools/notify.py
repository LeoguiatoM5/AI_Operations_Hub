"""Ferramenta de notificacao -- a primeira acao de ESCRITA do projeto.

A ferramenta nao conhece Slack, e-mail nem webhook: ela conversa com o Protocol
`Notifier`. O canal real (Slack, no V4.3) entra como mais uma implementacao, sem que a
ferramenta, o registro ou o grafo mudem uma linha.

`MemoryNotifier` e o equivalente do `FakeLLMProvider` aqui: o padrao do projeto. Quem
clona o repositorio consegue exercitar o fluxo completo de aprovacao humana -- inclusive
ver a mensagem "enviada" -- sem configurar integracao nenhuma.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.db.types import utcnow
from app.tools.base import ToolResult, ToolScope

logger = get_logger(__name__)


class NotifyInput(BaseModel):
    """Argumentos da notificacao.

    Os limites de tamanho nao sao decorativos: este payload e gerado por um LLM. Sem
    teto, uma alucinacao longa vira uma mensagem de 40 mil caracteres em um canal de
    equipe -- e, no dia em que o canal for pago por volume, vira conta.
    """

    title: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=4_000)
    channel: str = Field(
        default="geral",
        min_length=1,
        max_length=64,
        description="Canal de destino, na nomenclatura do notificador em uso.",
    )


@dataclass(frozen=True)
class Delivery:
    """Comprovante de entrega devolvido pelo notificador."""

    channel: str
    reference: str
    delivered_at: datetime


@runtime_checkable
class Notifier(Protocol):
    """Entrega uma mensagem em algum canal."""

    @property
    def name(self) -> str: ...

    async def send(self, *, title: str, body: str, channel: str) -> Delivery:
        """Entrega a mensagem e devolve o comprovante.

        Deve levantar excecao em caso de falha. Um notificador que engole erro
        transformaria "a mensagem nao chegou" em "tudo certo" -- exatamente o tipo de
        mentira que uma acao aprovada por humano nao pode contar.

        `channel` e o destino PEDIDO, e nao necessariamente o destino final: alguns
        canais tem endereco fixo na propria credencial. Por isso o comprovante devolve o
        destino real em `Delivery.channel`, que pode divergir do pedido.
        """
        ...

    async def aclose(self) -> None:
        """Libera recursos (conexoes HTTP, sessoes). Chamado no encerramento da app."""
        ...


@dataclass
class MemoryNotifier:
    """Notificador em memoria, para desenvolvimento local e testes.

    Guarda o que foi enviado em `sent`, o que permite ao teste afirmar sobre o CONTEUDO
    da mensagem -- e nao apenas que a chamada aconteceu.
    """

    sent: list[Delivery] = field(default_factory=list)
    messages: list[NotifyInput] = field(default_factory=list)

    @property
    def name(self) -> str:
        return "memory"

    async def send(self, *, title: str, body: str, channel: str) -> Delivery:
        self.messages.append(NotifyInput(title=title, body=body, channel=channel))
        entrega = Delivery(
            channel=channel,
            reference=f"memory-{len(self.messages)}",
            delivered_at=utcnow(),
        )
        self.sent.append(entrega)
        logger.info("notification_sent", notifier=self.name, channel=channel, title=title)
        return entrega

    async def aclose(self) -> None:
        """Nao ha recurso a liberar."""
        return None


class NotifyTool:
    """Envia uma mensagem para um canal da equipe."""

    def __init__(self, notifier: Notifier) -> None:
        self._notifier = notifier

    @property
    def name(self) -> str:
        return "send_notification"

    @property
    def description(self) -> str:
        return (
            "Envia uma mensagem de texto para um canal da equipe. "
            "Use para avisar pessoas sobre um resultado, um alerta ou uma pendencia."
        )

    @property
    def scope(self) -> ToolScope:
        return ToolScope.WRITE

    @property
    def input_model(self) -> type[NotifyInput]:
        return NotifyInput

    async def run(self, payload: NotifyInput) -> ToolResult:
        entrega = await self._notifier.send(
            title=payload.title, body=payload.body, channel=payload.channel
        )
        return ToolResult(
            tool=self.name,
            summary=f"Mensagem '{payload.title}' enviada para o canal '{entrega.channel}'.",
            output={
                "channel": entrega.channel,
                "reference": entrega.reference,
                "delivered_at": entrega.delivered_at.isoformat(),
                "notifier": self._notifier.name,
            },
        )
