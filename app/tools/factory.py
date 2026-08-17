"""Construcao do registro de ferramentas.

O registro e montado em um lugar so. Se cada consumidor montasse o seu, o servidor MCP
(V6) acabaria publicando um conjunto de ferramentas diferente do que o grafo executa --
e a divergencia so apareceria em producao.
"""

from app.core.config import Settings
from app.core.exceptions import ConfigurationError
from app.core.logging import get_logger
from app.rag.retriever import Retriever
from app.tools.knowledge import SearchKnowledgeTool
from app.tools.notify import MemoryNotifier, Notifier, NotifyTool
from app.tools.registry import ToolRegistry
from app.tools.slack import SlackNotifier

logger = get_logger(__name__)


def build_notifier(settings: Settings) -> Notifier:
    """Devolve o canal de notificacao configurado.

    A validacao acontece na construcao, e nao no envio. Descobrir que falta a URL do
    webhook no momento em que uma pessoa clica "aprovar" seria o pior lugar possivel:
    a acao ja foi autorizada, e a falha aparece como erro de infraestrutura em vez de
    erro de configuracao.
    """
    notifier: Notifier
    match settings.notifier:
        case "memory":
            notifier = MemoryNotifier()
        case "slack":
            if settings.slack_webhook_url is None:
                raise ConfigurationError(
                    "NOTIFIER=slack exige SLACK_WEBHOOK_URL definida.",
                    details={"notifier": "slack"},
                )
            notifier = SlackNotifier(
                webhook_url=settings.slack_webhook_url.get_secret_value(),
                destination=settings.slack_destination,
                timeout_seconds=settings.notifier_timeout_seconds,
            )
        case _:
            raise ConfigurationError(f"Notificador nao suportado: {settings.notifier!r}.")

    logger.info("notifier_built", notifier=notifier.name)
    return notifier


def build_tool_registry(*, retriever: Retriever, notifier: Notifier) -> ToolRegistry:
    """Monta o catalogo com as ferramentas desta execucao."""
    registry = ToolRegistry(
        [
            SearchKnowledgeTool(retriever),
            NotifyTool(notifier),
        ]
    )
    logger.debug("tool_registry_built", tools=[tool.name for tool in registry])
    return registry
