"""Ponto de entrada da aplicacao.

A aplicacao e criada por uma factory (`create_app`) em vez de um objeto global montado
na importacao. Isso permite que cada teste construa uma instancia limpa, com
configuracao, banco e provider proprios, sem estado vazando entre casos.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import monotonic

from fastapi import FastAPI
from langgraph.checkpoint.base import BaseCheckpointSaver
from sqlalchemy.ext.asyncio import AsyncEngine

from app import __version__
from app.api.errors import register_exception_handlers
from app.api.middleware import CorrelationIdMiddleware
from app.api.routes import (
    agents,
    approvals,
    chat,
    documents,
    executions,
    health,
    rag,
    tools,
)
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import create_engine, create_schema, create_session_factory
from app.integrations.callback import ResultPublisher, build_result_publisher
from app.llm.base import LLMProvider
from app.llm.factory import build_llm_provider
from app.rag.base import EmbeddingProvider, VectorStore
from app.rag.factory import build_embedding_provider, build_vector_store
from app.tools.factory import build_notifier
from app.tools.notify import Notifier
from app.workflows.checkpointer import create_checkpointer

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Ciclo de vida: prepara o schema na subida, libera as conexoes na descida."""
    settings: Settings = app.state.settings
    engine: AsyncEngine = app.state.engine

    await create_schema(engine)

    # Criado aqui, e nao em create_app, porque exige I/O assincrono. Testes e scripts
    # injetam o proprio (normalmente um MemorySaver) e nunca passam por este caminho.
    checkpoint_connection = None
    if app.state.checkpointer is None:
        app.state.checkpointer, checkpoint_connection = await create_checkpointer(settings)

    logger.info(
        "application_started",
        app=settings.app_name,
        version=__version__,
        environment=settings.app_env,
        llm_provider=app.state.llm_provider.name,
    )
    yield

    await app.state.llm_provider.aclose()
    await app.state.embedding_provider.aclose()
    await app.state.vector_store.aclose()
    await app.state.notifier.aclose()
    await app.state.publisher.aclose()
    if checkpoint_connection is not None:
        # O AsyncSqliteSaver nao fecha a conexao, e uma conexao SQLite pendurada mantem
        # o arquivo bloqueado no Windows.
        await checkpoint_connection.close()
    await engine.dispose()
    logger.info("application_stopped")


def create_app(
    settings: Settings | None = None,
    *,
    engine: AsyncEngine | None = None,
    llm_provider: LLMProvider | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    vector_store: VectorStore | None = None,
    checkpointer: BaseCheckpointSaver[str] | None = None,
    notifier: Notifier | None = None,
    publisher: ResultPublisher | None = None,
) -> FastAPI:
    """Monta a aplicacao FastAPI.

    Todas as dependencias externas sao injetaveis para que os testes usem banco em
    memoria, provider deterministico e indice volatil -- sem tocar em rede nem em disco.
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
    app.state.embedding_provider = embedding_provider or build_embedding_provider(settings)
    app.state.vector_store = vector_store or build_vector_store(settings)
    app.state.notifier = notifier or build_notifier(settings)
    app.state.publisher = publisher or build_result_publisher(settings)
    #: `None` significa "o lifespan cria". Testes injetam um MemorySaver.
    app.state.checkpointer = checkpointer
    app.state.started_at = monotonic()

    app.add_middleware(CorrelationIdMiddleware)
    register_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(executions.router)
    app.include_router(documents.router)
    app.include_router(rag.router)
    app.include_router(agents.router)
    app.include_router(tools.router)
    app.include_router(approvals.router)

    return app


app = create_app()
