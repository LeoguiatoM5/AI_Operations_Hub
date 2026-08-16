"""Configuracao de logs estruturados.

Objetivo: um unico fluxo de saida, no mesmo formato, para eventos emitidos pelo
structlog (nosso codigo) e pelo logging padrao (uvicorn, bibliotecas de terceiros).
"""

import logging
import sys

import structlog
from structlog.typing import Processor

from app.core.config import Settings


def configure_logging(settings: Settings) -> None:
    """Aplica a configuracao global de logs. Deve ser chamada uma vez, no startup."""
    level = logging.getLevelNamesMapping()[settings.log_level]

    # Processadores aplicados a todo evento, venha ele do structlog ou do logging padrao.
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,  # injeta correlation_id e afins
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            # Entrega o evento ao ProcessorFormatter em vez de renderizar aqui.
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Cores apenas quando a saida e um terminal. Redirecionada para arquivo, pipe ou
    # log de container, a coloracao viraria sequencias ANSI no meio do texto.
    renderer: Processor = (
        structlog.processors.JSONRenderer()
        if settings.log_format == "json"
        else structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        # foreign_pre_chain trata os eventos que NAO vieram do structlog (uvicorn etc.),
        # normalizando-os antes da renderizacao final.
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # O uvicorn instala handlers proprios; removemos para que tudo passe pelo root.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Atalho tipado para obter um logger."""
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
