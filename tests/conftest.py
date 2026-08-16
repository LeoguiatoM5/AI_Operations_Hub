"""Fixtures compartilhadas por toda a suite.

Nenhum teste toca em rede, em disco ou em API paga:

- banco SQLite em memoria, criado e destruido por teste;
- provider de LLM deterministico, com roteiro programavel;
- cliente HTTP falando com a aplicacao em memoria via ASGITransport, sem abrir porta.

`StaticPool` e necessario porque, no SQLite em memoria, cada conexao nova abriria um
banco vazio. Reutilizando a mesma conexao, tudo no teste enxerga o mesmo banco.
"""

import json
from collections.abc import AsyncIterator, Callable
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
from app.repositories.execution_repository import ExecutionRepository

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


# --------------------------------------------------------------------- configuracao


@pytest.fixture
def settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        app_env="test",
        log_level="WARNING",
        log_format="console",
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


# --------------------------------------------------------------------- LLM


@pytest.fixture
def provider() -> FakeLLMProvider:
    """Provider que responde uma triagem valida em toda chamada."""
    return FakeLLMProvider(script=[triage_json()])


# --------------------------------------------------------------------- aplicacao


@pytest.fixture
def app(settings: Settings, engine: AsyncEngine, provider: FakeLLMProvider) -> FastAPI:
    return create_app(settings, engine=engine, llm_provider=provider)


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
