"""Fixtures compartilhadas por toda a suite.

Nenhum teste toca em rede, em disco ou em API paga:

- banco SQLite em memoria, criado e destruido por teste;
- provider de LLM deterministico, com roteiro programavel;
- cliente HTTP falando com a aplicacao em memoria via ASGITransport, sem abrir porta.

`StaticPool` e necessario porque, no SQLite em memoria, cada conexao nova abriria um
banco vazio. Reutilizando a mesma conexao, tudo no teste enxerga o mesmo banco.
"""

import json
import shutil
import tempfile
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # registra as tabelas em Base.metadata
from app.core.config import Settings
from app.db.base import Base
from app.db.session import create_session_factory
from app.llm.base import LLMProvider
from app.llm.fake_provider import FakeLLMProvider
from app.main import create_app
from app.rag.embeddings import FakeEmbeddingProvider
from app.rag.memory_store import InMemoryVectorStore
from app.repositories.document_repository import DocumentRepository
from app.repositories.execution_repository import ExecutionRepository
from app.services.document_service import DocumentService

# --------------------------------------------------------------------- dados de apoio

TRIAGE_PADRAO: dict[str, Any] = {
    "intent": "analise",
    "summary": "Analisar chamados criticos e produzir recomendacoes.",
    "entities": ["chamados criticos", "hoje"],
    "urgency": "alta",
    "requires_approval": False,
    "suggested_agents": ["research", "analysis", "reporter"],
    "confidence": 0.86,
}


def triage_json(**overrides: Any) -> str:
    """Resposta de triagem valida, com campos sobrescritiveis."""
    return json.dumps({**TRIAGE_PADRAO, **overrides}, ensure_ascii=False)


RESEARCH_PADRAO: dict[str, Any] = {
    "answered": True,
    "answer": "O prazo para solicitar reembolso e de 30 dias corridos.",
    "citations": [1],
    "confidence": 0.9,
}


def research_json(**overrides: Any) -> str:
    """Resposta de pesquisa valida, com campos sobrescritiveis."""
    return json.dumps({**RESEARCH_PADRAO, **overrides}, ensure_ascii=False)


# --------------------------------------------------------------------- sistema de arquivos


@pytest.fixture
def temp_dir() -> Iterator[Path]:
    """Diretorio temporario isolado, removido ao final do teste.

    Usa `mkdtemp` em vez do `tmp_path` do pytest porque este cria tudo sob um diretorio
    de NOME FIXO (`pytest-of-<usuario>`): se esse diretorio ficar inacessivel em alguma
    maquina, toda a suite para. Um nome sorteado a cada execucao nao tem esse ponto unico
    de falha.
    """
    path = Path(tempfile.mkdtemp(prefix="aiops-test-"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


# --------------------------------------------------------------------- configuracao


@pytest.fixture
def settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        app_env="test",
        log_level="WARNING",
        log_format="console",
        # Limite pequeno de proposito: torna o teste do limite viavel sem gerar um
        # arquivo de dez megabytes a cada execucao.
        max_upload_bytes=64 * 1024,
    )


# --------------------------------------------------------------------- banco


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    factory = create_session_factory(engine)
    async with factory() as session:
        yield session


@pytest.fixture
def executions(session: AsyncSession) -> ExecutionRepository:
    return ExecutionRepository(session)


@pytest.fixture
def documents(session: AsyncSession) -> DocumentRepository:
    return DocumentRepository(session)


# --------------------------------------------------------------------- LLM


@pytest.fixture
def provider() -> FakeLLMProvider:
    """Provider que responde uma triagem valida em toda chamada."""
    return FakeLLMProvider(script=[triage_json()])


# --------------------------------------------------------------------- aplicacao


@pytest.fixture
def embedder() -> FakeEmbeddingProvider:
    return FakeEmbeddingProvider()


@pytest.fixture
def vector_store() -> InMemoryVectorStore:
    return InMemoryVectorStore()


@pytest.fixture
def document_service(
    documents: DocumentRepository,
    embedder: FakeEmbeddingProvider,
    vector_store: InMemoryVectorStore,
    settings: Settings,
) -> DocumentService:
    return DocumentService(
        documents,
        embedder,
        vector_store,
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        max_size_bytes=settings.max_upload_bytes,
    )


@pytest.fixture
def app(
    settings: Settings,
    engine: AsyncEngine,
    provider: FakeLLMProvider,
    embedder: FakeEmbeddingProvider,
    vector_store: InMemoryVectorStore,
) -> FastAPI:
    return create_app(
        settings,
        engine=engine,
        llm_provider=provider,
        embedding_provider=embedder,
        vector_store=vector_store,
    )


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.fixture
async def make_client(
    settings: Settings, engine: AsyncEngine
) -> AsyncIterator[Callable[[LLMProvider], AsyncClient]]:
    """Constroi um cliente com um provider especifico.

    Util nos testes de falha, em que cada caso precisa de um roteiro diferente. Os
    clientes criados sao fechados ao final do teste.
    """
    created: list[AsyncClient] = []

    def build(llm_provider: LLMProvider) -> AsyncClient:
        application = create_app(settings, engine=engine, llm_provider=llm_provider)
        client = AsyncClient(transport=ASGITransport(app=application), base_url="http://testserver")
        created.append(client)
        return client

    yield build

    for client in created:
        await client.aclose()
