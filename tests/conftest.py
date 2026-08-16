"""Fixtures compartilhadas.

O cliente conversa com a aplicacao em memoria, via ASGITransport: nao sobe servidor,
nao abre porta e nao depende de rede. Testes de API ficam tao rapidos quanto unitarios.
"""

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def settings() -> Settings:
    return Settings(app_env="test", log_level="WARNING", log_format="console")


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
