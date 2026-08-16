"""Ponto de entrada da aplicacao.

A aplicacao e criada por uma factory (`create_app`) em vez de um objeto global
montado na importacao. Isso permite que cada teste construa uma instancia limpa,
com configuracao propria, sem estado vazando entre casos.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import monotonic

from fastapi import FastAPI

from app import __version__
from app.api.errors import register_exception_handlers
from app.api.middleware import CorrelationIdMiddleware
from app.api.routes import health
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Ciclo de vida da aplicacao: recursos caros sobem aqui e sao liberados no fim."""
    settings: Settings = app.state.settings
    logger.info(
        "application_started",
        app=settings.app_name,
        version=__version__,
        environment=settings.app_env,
    )
    yield
    logger.info("application_stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Monta a aplicacao FastAPI."""
    settings = settings or get_settings()
    configure_logging(settings)

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        summary="Plataforma de automacao empresarial orientada a agentes de IA",
        lifespan=lifespan,
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
    )

    # Estado definido aqui, e nao no lifespan, porque o cliente de teste do httpx nao
    # executa o lifespan. Quando houver recurso caro de verdade (engine do banco),
    # ele vai para o lifespan e o teste passa a usar um gerenciador de lifespan.
    # A configuracao fica no estado da app para que as rotas a recebam por injecao
    # (app/api/deps.py), em vez de alcancarem o singleton global.
    app.state.settings = settings
    app.state.started_at = monotonic()

    app.add_middleware(CorrelationIdMiddleware)
    register_exception_handlers(app)
    app.include_router(health.router)

    return app


app = create_app()
