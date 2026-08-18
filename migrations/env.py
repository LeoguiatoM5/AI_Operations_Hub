"""Ambiente do Alembic.

**A URL do banco vem de `Settings`, e nao do `alembic.ini`.** Um `sqlalchemy.url` fixo no
ini teria de ser mantido em sincronia com a configuracao da aplicacao a mao -- e a hora em
que os dois divergem e a hora em que alguem roda uma migracao no banco errado.

O `alembic.ini` fica sem URL de proposito: se alguem a colocar la, ela sera ignorada, e
esse silencio e pior que o erro. A leitura vem por `app.core.config`, que ja valida tudo
na inicializacao e ja sabe ler `.env`.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy.types import TypeDecorator

import app.models  # noqa: F401  -- registra as tabelas em Base.metadata
from app.core.config import get_settings
from app.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

#: O que o Alembic compara com o banco para gerar migracoes automaticas.
target_metadata = Base.metadata


def database_url() -> str:
    """URL efetiva, com a mesma precedencia da aplicacao.

    `-x url=...` na linha de comando vence, e existe para o caso de rodar contra um banco
    que nao e o configurado -- tipicamente um banco descartavel de teste de migracao.
    """
    argumentos = context.get_x_argument(as_dictionary=True)
    return argumentos.get("url") or get_settings().database_url


def include_object(objeto: object, nome: str | None, tipo: str, reflected: bool, comparar) -> bool:  # type: ignore[no-untyped-def]
    """Filtra o que entra na comparacao.

    As tabelas de checkpoint do LangGraph vivem em OUTRO arquivo de banco e tem outro dono
    -- a biblioteca as cria sozinha. Se elas aparecessem aqui, o `--autogenerate` proporia
    remove-las a cada execucao.
    """
    return not (
        tipo == "table" and nome in {"checkpoints", "checkpoint_blobs", "checkpoint_writes"}
    )


def render_item(tipo: str, objeto: object, autogen_context: object) -> str | bool:
    """Como cada item aparece no arquivo de migracao.

    **Os tipos personalizados do projeto sao desembrulhados para o tipo do banco.**
    `UtcDateTime` e `StrEnumType` sao `TypeDecorator`: conveniencias do lado Python que,
    no banco, sao `DateTime` e `String`. Sem isto o Alembic escreve
    `app.db.types.StrEnumType(length=16)` na migracao -- e ai duas coisas dao errado:

    1. o arquivo nao importa `app`, entao a migracao quebra com `NameError` ao rodar;
    2. mesmo com o import, a migracao passaria a **depender do codigo da aplicacao**. Uma
       migracao antiga precisa continuar rodando daqui a dois anos, quando aquela classe
       pode ter mudado de nome ou deixado de existir. Migracao descreve o banco, e o banco
       nao sabe o que e um `StrEnum`.
    """
    if tipo == "type" and isinstance(objeto, TypeDecorator):
        interno = objeto.impl
        if isinstance(interno, type):
            interno = interno()
        return f"sa.{interno.__class__.__name__}({_type_args(interno)})"
    return False


def _type_args(tipo: object) -> str:
    tamanho = getattr(tipo, "length", None)
    return f"length={tamanho}" if tamanho else ""


def run_migrations_offline() -> None:
    """Gera o SQL sem conectar. Util para revisar o que sera aplicado em producao."""
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
        render_item=render_item,
        # SQLite nao suporta ALTER na maioria dos casos: o Alembic recria a tabela e copia
        # os dados. Sem isto, qualquer migracao que altere coluna falha no dialeto que o
        # projeto usa em desenvolvimento.
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
        render_item=render_item,
        render_as_batch=connection.dialect.name == "sqlite",
        # Sem isto, mudar `String(64)` para `String(128)` passaria despercebido pelo
        # autogenerate -- e o desvio entre modelo e banco so apareceria em producao.
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    configuracao = config.get_section(config.config_ini_section, {})
    configuracao["sqlalchemy.url"] = database_url()

    engine = async_engine_from_config(configuracao, prefix="sqlalchemy.", poolclass=pool.NullPool)
    async with engine.connect() as conexao:
        await conexao.run_sync(do_run_migrations)
    await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
