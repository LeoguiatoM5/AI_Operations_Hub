"""Persistencia do estado do grafo.

O checkpointer grava o estado apos cada no. Hoje ele serve a inspecao e a retomada apos
falha; no V4 e ele que torna possivel o `WAITING_APPROVAL` sobreviver a um restart -- o
grafo pausa em um no e retoma daquele ponto exato, em vez de reexecutar tudo (e pagar
todos os tokens) de novo.

Sem checkpointer, "aprovacao humana" seria apenas uma coluna no banco.
"""

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.core.config import Settings
from app.core.logging import get_logger
from app.db.session import ensure_sqlite_directory

logger = get_logger(__name__)


async def create_checkpointer(settings: Settings) -> tuple[AsyncSqliteSaver, aiosqlite.Connection]:
    """Cria o checkpointer e devolve a conexao para fechamento no encerramento.

    A conexao volta junto de proposito: o `AsyncSqliteSaver` nao a fecha, e uma conexao
    SQLite pendurada mantem o arquivo bloqueado no Windows.
    """
    ensure_sqlite_directory(f"sqlite+aiosqlite:///{settings.checkpoint_path}")
    connection = await aiosqlite.connect(settings.checkpoint_path)
    saver = AsyncSqliteSaver(connection)
    await saver.setup()
    logger.info("checkpointer_ready", path=settings.checkpoint_path)
    return saver, connection
