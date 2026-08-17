"""Entrega do resultado de uma execucao a um sistema externo.

**Por que isto existe.** Quem chama `POST /agents/run` recebe o resultado na resposta --
exceto quando o grafo pausa para aprovacao humana. Nesse caso a resposta sai como
`waiting_approval`, e o resultado de verdade so existe depois que uma pessoa decidir,
possivelmente horas depois. A essa altura nenhum cliente HTTP esta mais esperando.

Sem este modulo o n8n seria decorativo: dispararia o Hub e nunca saberia como terminou.

**A regra que governa o modulo inteiro:** falhar aqui nao pode desfazer nada. Quando o
callback roda, a acao ja foi executada e a aprovacao ja esta gravada. Se o n8n estiver
fora do ar, o certo e registrar e seguir -- levantar excecao transformaria "o aviso nao
chegou" em "a execucao falhou", o que seria mentira sobre um trabalho que deu certo.
"""

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

import httpx

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@runtime_checkable
class ResultPublisher(Protocol):
    """Entrega o resultado final de uma execucao a quem estiver interessado."""

    @property
    def name(self) -> str: ...

    async def publish(self, payload: Mapping[str, Any]) -> bool:
        """Entrega o resultado. Devolve se conseguiu.

        **Nunca levanta excecao.** O booleano existe para o log e para os testes; quem
        chama nao precisa tratar falha, porque nao ha nada a fazer com ela.
        """
        ...

    async def aclose(self) -> None: ...


class NullPublisher:
    """Nao publica em lugar nenhum. E o padrao.

    Sem callback configurado, o comportamento correto e nao fazer nada -- e nao guardar
    payloads em memoria a espera de alguem, que vazaria memoria num processo longo.
    """

    @property
    def name(self) -> str:
        return "none"

    async def publish(self, payload: Mapping[str, Any]) -> bool:
        return False

    async def aclose(self) -> None:
        return None


class MemoryPublisher:
    """Guarda os payloads em vez de envia-los. Para testes e para inspecao local."""

    def __init__(self) -> None:
        self.published: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return "memory"

    async def publish(self, payload: Mapping[str, Any]) -> bool:
        self.published.append(dict(payload))
        return True

    async def aclose(self) -> None:
        return None


class WebhookPublisher:
    """Faz POST do resultado em uma URL -- tipicamente um webhook do n8n."""

    def __init__(
        self,
        *,
        url: str,
        timeout_seconds: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = url
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)

    @property
    def name(self) -> str:
        return "webhook"

    async def publish(self, payload: Mapping[str, Any]) -> bool:
        try:
            response = await self._client.post(self._url, json=dict(payload))
        except httpx.HTTPError as error:
            # Engolir excecao e o comportamento CORRETO aqui, e so aqui: a acao ja
            # aconteceu, e o callback e um aviso sobre ela. Compare com
            # `SlackNotifier.send` (ED-050), onde engolir o erro esconderia que a acao
            # aprovada nao foi executada.
            logger.warning(
                "result_callback_failed",
                publisher=self.name,
                error_type=type(error).__name__,
                execution_id=payload.get("execution_id"),
            )
            return False

        entregue = response.is_success
        logger.info(
            "result_callback_published",
            publisher=self.name,
            status_code=response.status_code,
            delivered=entregue,
            execution_id=payload.get("execution_id"),
        )
        return entregue

    async def aclose(self) -> None:
        await self._client.aclose()


def build_result_publisher(settings: Settings) -> ResultPublisher:
    """Devolve o publicador configurado.

    Nao ha seletor `RESULT_CALLBACK=none|webhook`: a presenca da URL ja determina a
    escolha. Um seletor separado permitiria o estado invalido "webhook selecionado, URL
    ausente" -- e configuracao que aceita combinacao impossivel acaba produzindo erro em
    runtime no lugar mais inconveniente.

    (Contraste com `NOTIFIER`, onde o seletor faz sentido: `memory` e `slack` sao duas
    escolhas validas, e nenhum campo distingue uma da outra por si so.)
    """
    if not settings.result_callback_url:
        return NullPublisher()

    publisher = WebhookPublisher(
        url=settings.result_callback_url,
        timeout_seconds=settings.result_callback_timeout_seconds,
    )
    logger.info("result_publisher_built", publisher=publisher.name)
    return publisher
