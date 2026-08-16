"""Tratamento centralizado de erros.

Toda falha sai da API no mesmo envelope, com o correlation ID incluido -- assim o
usuario da API consegue reportar um problema e nos localizamos o rastro exato nos logs.
Detalhes internos (stack trace, mensagens de excecao nao previstas) ficam no log,
nunca na resposta.
"""

from typing import cast

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import Response

from app.api.middleware import CORRELATION_ID_HEADER
from app.core.exceptions import AIHubError
from app.core.logging import get_logger
from app.schemas.common import ErrorBody, ErrorResponse

logger = get_logger(__name__)


def _correlation_id(request: Request) -> str | None:
    return cast(str | None, getattr(request.state, "correlation_id", None))


def _build_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: dict[str, object] | None = None,
) -> JSONResponse:
    correlation_id = _correlation_id(request)
    payload = ErrorResponse(
        error=ErrorBody(
            code=code,
            message=message,
            details=details or {},
            correlation_id=correlation_id,
        )
    )
    headers = {CORRELATION_ID_HEADER: correlation_id} if correlation_id else None
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json"),
        headers=headers,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Registra os tratadores na aplicacao."""

    async def handle_domain_error(request: Request, exc: Exception) -> Response:
        error = cast(AIHubError, exc)
        logger.warning(
            "domain_error",
            code=error.code,
            message=error.message,
            details=error.details,
        )
        return _build_response(
            request,
            status_code=error.http_status,
            code=error.code,
            message=error.message,
            details=error.details,
        )

    async def handle_http_error(request: Request, exc: Exception) -> Response:
        error = cast(StarletteHTTPException, exc)
        return _build_response(
            request,
            status_code=error.status_code,
            code="http_error",
            message=str(error.detail),
        )

    async def handle_request_validation(request: Request, exc: Exception) -> Response:
        error = cast(RequestValidationError, exc)
        logger.info("request_validation_error", errors=error.errors())
        return _build_response(
            request,
            status_code=422,
            code="validation_error",
            message="Payload invalido.",
            details={"errors": error.errors()},
        )

    async def handle_unexpected(request: Request, exc: Exception) -> Response:
        # O stack trace vai para o log; a resposta nao expoe detalhes internos.
        logger.exception("unhandled_error", error_type=type(exc).__name__)
        return _build_response(
            request,
            status_code=500,
            code="internal_error",
            message="Erro interno inesperado.",
        )

    app.add_exception_handler(AIHubError, handle_domain_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_error)
    app.add_exception_handler(RequestValidationError, handle_request_validation)
    app.add_exception_handler(Exception, handle_unexpected)
