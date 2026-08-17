"""Dependencias do servidor MCP.

O FastAPI monta as dependencias por requisicao com `Depends`. O MCP nao tem requisicao --
tem um processo de vida longa falando por stdio. Este modulo e o equivalente: constroi
uma vez o que e caro (engine, providers, indice, checkpointer) e entrega por chamada o
que precisa de escopo curto (a sessao de banco).

**A regra que este arquivo existe para respeitar:** nenhuma regra de negocio mora aqui.
Ele monta `RagService`, `WorkflowService` e os repositorios -- os mesmos que a API usa --
e nada mais. Se uma decisao de produto aparecer neste arquivo, o servidor MCP deixou de
ser um adaptador e virou uma segunda implementacao do sistema.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import aiosqlite
from langgraph.checkpoint.base import BaseCheckpointSaver
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.agents.research import ResearchAgent
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import create_engine, create_schema, create_session_factory, session_scope
from app.integrations.callback import ResultPublisher, build_result_publisher
from app.llm.base import LLMProvider
from app.llm.factory import build_llm_provider
from app.quality.engine import QualityEngine
from app.quality.factory import build_quality_engine
from app.rag.base import EmbeddingProvider, VectorStore
from app.rag.factory import build_embedding_provider, build_vector_store
from app.rag.retriever import Retriever
from app.repositories.approval_repository import ApprovalRepository
from app.repositories.document_repository import DocumentRepository
from app.repositories.execution_repository import ExecutionRepository
from app.services.rag_service import RagService
from app.services.workflow_service import WorkflowService
from app.tools.factory import build_notifier, build_tool_registry
from app.tools.notify import Notifier
from app.workflows.checkpointer import create_checkpointer

logger = get_logger(__name__)


@dataclass
class ServiceContainer:
    """O que vive enquanto o servidor viver."""

    settings: Settings
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    llm: LLMProvider
    embedder: EmbeddingProvider
    store: VectorStore
    notifier: Notifier
    publisher: ResultPublisher
    checkpointer: BaseCheckpointSaver[str]
    quality: QualityEngine | None
    _checkpoint_connection: aiosqlite.Connection | None = None

    # ------------------------------------------------------------------ por chamada

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Sessao com transacao por chamada de ferramenta.

        Cada chamada MCP e uma unidade de trabalho, como um request HTTP: commit no fim
        se deu certo, rollback em qualquer excecao. Uma sessao de vida longa acumularia o
        estado de chamadas nao relacionadas e transformaria um erro em qualquer uma delas
        num rollback de todas.
        """
        async with session_scope(self.session_factory) as session:
            yield session

    def retriever(self) -> Retriever:
        return Retriever(
            self.embedder,
            self.store,
            top_k=self.settings.rag_top_k,
            min_score=self.settings.rag_min_score,
        )

    def rag_service(self, session: AsyncSession) -> RagService:
        return RagService(
            self.retriever(),
            ResearchAgent(self.llm),
            DocumentRepository(session),
            embedding_model=self.embedder.model,
        )

    def workflow_service(self, session: AsyncSession) -> WorkflowService:
        return WorkflowService(
            ExecutionRepository(session),
            self.llm,
            self.retriever(),
            build_tool_registry(retriever=self.retriever(), notifier=self.notifier),
            ApprovalRepository(session),
            self.checkpointer,
            self.publisher,
            self.quality,
        )

    @staticmethod
    def executions(session: AsyncSession) -> ExecutionRepository:
        return ExecutionRepository(session)

    @staticmethod
    def documents(session: AsyncSession) -> DocumentRepository:
        return DocumentRepository(session)

    @staticmethod
    def approvals(session: AsyncSession) -> ApprovalRepository:
        return ApprovalRepository(session)


@asynccontextmanager
async def build_container(
    settings: Settings | None = None, **overrides: Any
) -> AsyncIterator[ServiceContainer]:
    """Monta o container e o desmonta ao final.

    Args:
        overrides: substitui qualquer dependencia pelo nome do campo. E o que permite ao
            teste rodar o servidor inteiro com banco em memoria e provider falso, sem
            tocar rede nem disco -- o mesmo mecanismo de `create_app()`.
    """
    settings = settings or get_settings()
    configure_logging(settings)

    engine: AsyncEngine = overrides.get("engine") or create_engine(settings)
    await create_schema(engine)

    llm = overrides.get("llm") or build_llm_provider(settings)
    embedder = overrides.get("embedder") or build_embedding_provider(settings)
    store = overrides.get("store") or build_vector_store(settings)
    notifier = overrides.get("notifier") or build_notifier(settings)
    publisher = overrides.get("publisher") or build_result_publisher(settings)

    checkpointer = overrides.get("checkpointer")
    conexao: aiosqlite.Connection | None = None
    if checkpointer is None:
        checkpointer, conexao = await create_checkpointer(settings)

    container = ServiceContainer(
        settings=settings,
        engine=engine,
        session_factory=create_session_factory(engine),
        llm=llm,
        embedder=embedder,
        store=store,
        notifier=notifier,
        publisher=publisher,
        checkpointer=checkpointer,
        quality=(
            build_quality_engine(llm, threshold=settings.quality_threshold)
            if settings.quality_enabled
            else None
        ),
        _checkpoint_connection=conexao,
    )

    logger.info(
        "mcp_container_ready",
        llm_provider=llm.name,
        embedding_provider=embedder.name,
        vector_store=store.name,
        quality_enabled=container.quality is not None,
    )

    try:
        yield container
    finally:
        await llm.aclose()
        await embedder.aclose()
        await store.aclose()
        await notifier.aclose()
        await publisher.aclose()
        if conexao is not None:
            await conexao.close()
        if overrides.get("engine") is None:
            await engine.dispose()
