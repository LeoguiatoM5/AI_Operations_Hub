"""Engine, fabrica de sessoes e criacao do schema."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings
from app.core.logging import get_logger
from app.db.base import Base

logger = get_logger(__name__)

SQLITE_FILE_PREFIX = "sqlite+aiosqlite:///"


def ensure_sqlite_directory(database_url: str) -> None:
    """Cria o diretorio do arquivo SQLite, se necessario.

    Sem isso, a primeira execucao em uma maquina limpa falha com "unable to open
    database file" -- erro que nao diz que o problema e uma pasta inexistente.
    """
    if not database_url.startswith(SQLITE_FILE_PREFIX):
        return
    raw_path = database_url.removeprefix(SQLITE_FILE_PREFIX)
    if not raw_path or raw_path.startswith(":memory:"):
        return
    Path(raw_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


def create_engine(settings: Settings) -> AsyncEngine:
    """Cria o engine assincrono.

    Assincrono porque a API e assincrona: um driver bloqueante travaria o event loop a
    cada consulta, anulando a concorrencia conquistada na camada de LLM.
    """
    ensure_sqlite_directory(settings.database_url)
    return create_async_engine(
        settings.database_url,
        echo=settings.database_echo,
        # Detecta conexoes derrubadas pelo servidor antes de usa-las. Irrelevante no
        # SQLite, essencial quando o destino for PostgreSQL.
        pool_pre_ping=True,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Cria a fabrica de sessoes."""
    return async_sessionmaker(
        bind=engine,
        expire_on_commit=False,
        autoflush=False,
    )


async def create_schema(engine: AsyncEngine) -> None:
    """Cria as tabelas ausentes, direto do modelo.

    **Nao substitui as migracoes -- convive com elas, e a fronteira e clara.**

    `create_all` serve a banco descartavel: o SQLite em memoria da suite, o indice
    temporario do `run_evals.py`, um clone recem-baixado que sobe em cinco segundos. E
    rapido, nao precisa de historico e nao tem o que preservar.

    Migracao serve a banco que guarda dados de alguem. Ela sabe **alterar** uma tabela
    existente sem perder o conteudo, que e exatamente o que `create_all` nao faz: ele cria
    o que falta e ignora o que mudou. Uma coluna renomeada no modelo simplesmente nao
    aparece, e o desvio so vira erro na primeira consulta.

    O risco de ter dois caminhos e divergirem. `tests/integration/test_migrations.py` roda
    `alembic check` e falha se um modelo mudou sem a migracao correspondente.

    Em PostgreSQL, este metodo **nao cria nada**: la o esquema vem de `alembic upgrade
    head`, e criar tabela por baixo das migracoes deixaria o banco num estado que o
    Alembic nao reconhece.
    """
    if engine.dialect.name != "sqlite":
        logger.info(
            "database_schema_skipped",
            dialect=engine.dialect.name,
            reason="esquema gerenciado por migracao: rode `alembic upgrade head`",
        )
        return

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    logger.info("database_schema_ready", tables=sorted(Base.metadata.tables))


@asynccontextmanager
async def session_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Sessao com transacao encerrada corretamente.

    Commit no caminho feliz, rollback em qualquer excecao. Deixar isso a cargo de quem
    chama e como surgem transacoes penduradas e escritas parciais.
    """
    async with session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()
