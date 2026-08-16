"""Ponto de entrada da aplicacao.

A aplicacao e criada por uma factory (`create_app`) em vez de um objeto global montado
na importacao. Isso permite que cada teste construa uma instancia limpa, com
configuracao, banco e provider proprios, sem estado vazando entre casos.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import monotonic

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from app import __version__
from app.api.errors import register_exception_handlers
from app.api.middleware import CorrelationIdMiddleware
from app.api.routes import chat, executions, health
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import create_engine, create_schema, create_session_factory
from app.llm.base import LLMProvider
from app.llm.factory import build_llm_provider

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Ciclo de vida: prepara o schema na subida, libera as conexoes na descida."""
    settings: Settings = app.state.settings
    engine: AsyncEngine = app.state.engine

    await create_schema(engine)
    logger.info(
        "application_started",
        app=settings.app_name,
        version=__version__,
        environment=settings.app_env,
        llm_provider=app.state.llm_provider.name,
    )
    yield

    await app.state.llm_provider.aclose()
    await engine.dispose()
    logger.info("application_stopped")


def create_app(
    settings: Settings | None = None,
    *,
    engine: AsyncEngine | None = None,
    llm_provider: LLMProvider | None = None,
) -> FastAPI:
    """Monta a aplicacao FastAPI.

    `engine` e `llm_provider` sao injetaveis para que os testes usem um banco em memoria
    e um provider deterministico, sem tocar em rede nem em disco.
    """
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

    # Estado montado aqui, e nao no lifespan, porque o cliente de teste do httpx nao
    # executa o lifespan. O que exige I/O (criar o schema) fica no lifespan; o que e
    # apenas construcao de objeto fica aqui.
    app.state.settings = settings
    app.state.engine = engine or create_engine(settings)
    app.state.session_factory = create_session_factory(app.state.engine)
    app.state.llm_provider = llm_provider or build_llm_provider(settings)
    app.state.started_at = monotonic()

    app.add_middleware(CorrelationIdMiddleware)
    register_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(executions.router)

    return app


app = create_app()
