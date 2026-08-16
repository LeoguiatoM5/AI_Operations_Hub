"""Dependencias injetaveis nas rotas.

Nenhum endpoint deve alcancar um objeto global diretamente. Tudo que uma rota precisa
chega por injecao, a partir do estado da aplicacao montada pela factory. Isso mantem
`create_app(...)` honesta -- o que e passado e o que e usado -- e permite substituir
qualquer dependencia em teste.
"""

from collections.abc import AsyncIterator
from typing import Annotated, cast

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.requests import Request

from app.agents.research import ResearchAgent
from app.agents.triage import TriageAgent
from app.core.config import Settings
from app.db.session import session_scope
from app.llm.base import LLMProvider
from app.rag.base import EmbeddingProvider, VectorStore
from app.rag.retriever import Retriever
from app.repositories.document_repository import DocumentRepository
from app.repositories.execution_repository import ExecutionRepository
from app.services.document_service import DocumentService
from app.services.execution_service import ExecutionService
from app.services.rag_service import RagService


def get_app_settings(request: Request) -> Settings:
    """Configuracao efetiva desta instancia da aplicacao."""
    return cast(Settings, request.app.state.settings)


def get_correlation_id(request: Request) -> str | None:
    """Identificador do request atual, definido pelo CorrelationIdMiddleware."""
    return cast(str | None, getattr(request.state, "correlation_id", None))


def get_llm_provider(request: Request) -> LLMProvider:
    """Provider construido uma vez no startup, reaproveitado por todos os requests."""
    return cast(LLMProvider, request.app.state.llm_provider)


def get_embedding_provider(request: Request) -> EmbeddingProvider:
    return cast(EmbeddingProvider, request.app.state.embedding_provider)


def get_vector_store(request: Request) -> VectorStore:
    """Indice compartilhado pela aplicacao inteira.

    Um por processo, e nao um por request: abrir o Chroma custa I/O de disco, e o
    indice e recurso global, nao estado de uma requisicao.
    """
    return cast(VectorStore, request.app.state.vector_store)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Sessao de banco com transacao por request.

    Commit ao final do request bem-sucedido, rollback em qualquer excecao. O escopo e o
    request inteiro para que a rota nao precise decidir quando confirmar a transacao.
    """
    factory = cast(async_sessionmaker[AsyncSession], request.app.state.session_factory)
    async with session_scope(factory) as session:
        yield session


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
CorrelationIdDep = Annotated[str | None, Depends(get_correlation_id)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
LLMProviderDep = Annotated[LLMProvider, Depends(get_llm_provider)]
EmbeddingProviderDep = Annotated[EmbeddingProvider, Depends(get_embedding_provider)]
VectorStoreDep = Annotated[VectorStore, Depends(get_vector_store)]


def get_execution_repository(session: SessionDep) -> ExecutionRepository:
    return ExecutionRepository(session)


def get_document_repository(session: SessionDep) -> DocumentRepository:
    return DocumentRepository(session)


ExecutionRepositoryDep = Annotated[ExecutionRepository, Depends(get_execution_repository)]
DocumentRepositoryDep = Annotated[DocumentRepository, Depends(get_document_repository)]


def get_execution_service(
    repository: ExecutionRepositoryDep, provider: LLMProviderDep
) -> ExecutionService:
    return ExecutionService(repository, TriageAgent(provider))


def get_document_service(
    repository: DocumentRepositoryDep,
    embedder: EmbeddingProviderDep,
    store: VectorStoreDep,
    settings: SettingsDep,
) -> DocumentService:
    return DocumentService(
        repository,
        embedder,
        store,
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        max_size_bytes=settings.max_upload_bytes,
    )


def get_retriever(
    embedder: EmbeddingProviderDep, store: VectorStoreDep, settings: SettingsDep
) -> Retriever:
    return Retriever(
        embedder,
        store,
        top_k=settings.rag_top_k,
        # None mantem o piso declarado pelo provedor, que conhece a propria escala.
        min_score=settings.rag_min_score,
    )


RetrieverDep = Annotated[Retriever, Depends(get_retriever)]


def get_rag_service(
    retriever: RetrieverDep,
    provider: LLMProviderDep,
    embedder: EmbeddingProviderDep,
    repository: DocumentRepositoryDep,
) -> RagService:
    return RagService(
        retriever,
        ResearchAgent(provider),
        repository,
        embedding_model=embedder.model,
    )


ExecutionServiceDep = Annotated[ExecutionService, Depends(get_execution_service)]
DocumentServiceDep = Annotated[DocumentService, Depends(get_document_service)]
RagServiceDep = Annotated[RagService, Depends(get_rag_service)]
