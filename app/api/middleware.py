"""Middlewares HTTP.

O correlation ID e a espinha dorsal da observabilidade: um unico request pode
disparar varias chamadas de LLM, buscas vetoriais e webhooks. Sem um identificador
propagado por todo esse caminho, nao ha como reconstruir o que aconteceu.
"""

import re
from time import perf_counter
from uuid import uuid4

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import get_logger

CORRELATION_ID_HEADER = "X-Correlation-ID"

# O ID vem de fora e vai parar em arquivos de log. Restringimos o formato para
# evitar injecao de conteudo (quebras de linha, terminadores) na saida de log.
_SAFE_CORRELATION_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

logger = get_logger(__name__)


def _resolve_correlation_id(request: Request) -> str:
    """Reaproveita o ID enviado pelo cliente quando ele for seguro; senao gera um novo."""
    candidate = request.headers.get(CORRELATION_ID_HEADER)
    if candidate and _SAFE_CORRELATION_ID.match(candidate):
        return candidate
    return str(uuid4())


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Vincula um correlation ID ao contexto e registra inicio/fim de cada request."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        correlation_id = _resolve_correlation_id(request)

        # contextvars atravessa `await` corretamente: em um servidor async, uma
        # variavel global entregaria o ID do request vizinho.
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            correlation_id=correlation_id,
            method=request.method,
            path=request.url.path,
        )
        request.state.correlation_id = correlation_id

        started_at = perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "request_failed",
                duration_ms=round((perf_counter() - started_at) * 1000, 2),
            )
            raise

        duration_ms = round((perf_counter() - started_at) * 1000, 2)
        logger.info(
            "request_completed",
            status_code=response.status_code,
            duration_ms=duration_ms,
        )
        response.headers[CORRELATION_ID_HEADER] = correlation_id
        return response
