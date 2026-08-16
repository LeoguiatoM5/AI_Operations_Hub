"""Fixtures de banco de dados.

Cada teste recebe um banco SQLite em memoria, criado e destruido no proprio teste.
Isso da isolamento perfeito (nenhum teste enxerga dado de outro) sem custo de arquivo
em disco -- e permite rodar a suite em paralelo mais adiante.

`StaticPool` e necessario porque, no SQLite em memoria, cada conexao nova abriria um
banco novo e vazio. Reutilizando a mesma conexao, todas as operacoes do teste veem o
mesmo banco.
"""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import create_session_factory
from app.repositories.execution_repository import ExecutionRepository

# Importado pelo efeito colateral de registrar as tabelas em Base.metadata.
import app.models  # noqa: F401  isort:skip


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
