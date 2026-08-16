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

from app.agents.triage import TriageAgent
from app.core.config import Settings
from app.db.session import session_scope
from app.llm.base import LLMProvider
from app.repositories.execution_repository import ExecutionRepository
from app.services.execution_service import ExecutionService


def get_app_settings(request: Request) -> Settings:
    """Configuracao efetiva desta instancia da aplicacao."""
    return cast(Settings, request.app.state.settings)


def get_correlation_id(request: Request) -> str | None:
    """Identificador do request atual, definido pelo CorrelationIdMiddleware."""
    return cast(str | None, getattr(request.state, "correlation_id", None))


def get_llm_provider(request: Request) -> LLMProvider:
    """Provider construido uma vez no startup, reaproveitado por todos os requests."""
    return cast(LLMProvider, request.app.state.llm_provider)


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


def get_execution_repository(session: SessionDep) -> ExecutionRepository:
    return ExecutionRepository(session)


ExecutionRepositoryDep = Annotated[ExecutionRepository, Depends(get_execution_repository)]


def get_execution_service(
    repository: ExecutionRepositoryDep, provider: LLMProviderDep
) -> ExecutionService:
    return ExecutionService(repository, TriageAgent(provider))


ExecutionServiceDep = Annotated[ExecutionService, Depends(get_execution_service)]
