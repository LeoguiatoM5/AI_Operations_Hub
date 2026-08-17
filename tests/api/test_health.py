"""Testes do endpoint de saude e do contrato de erro da API."""

from fastapi import FastAPI
from httpx import AsyncClient

from app import __version__
from app.api.middleware import CORRELATION_ID_HEADER
from app.core.exceptions import NotFoundError


async def test_health_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert body["environment"] == "test"
    assert body["uptime_seconds"] >= 0


async def test_health_says_where_write_actions_go(client: AsyncClient) -> None:
    """Uma aprovacao significa coisas diferentes conforme o canal esteja ligado ou nao.
    Descobrir isso lendo o `.env` do servidor e mais dificil do que deveria."""
    body = (await client.get("/health")).json()

    assert body["notifier"] == "memory"


async def test_correlation_id_is_echoed_when_client_provides_one(client: AsyncClient) -> None:
    response = await client.get("/health", headers={CORRELATION_ID_HEADER: "pedido-123"})

    assert response.headers[CORRELATION_ID_HEADER] == "pedido-123"


async def test_correlation_id_is_generated_when_absent(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.headers[CORRELATION_ID_HEADER]


async def test_unsafe_correlation_id_is_replaced(client: AsyncClient) -> None:
    """Um ID com quebra de linha poderia forjar entradas falsas no arquivo de log."""
    malicious = "abc\nevent=admin_login"

    response = await client.get("/health", headers={CORRELATION_ID_HEADER: malicious})

    assert response.headers[CORRELATION_ID_HEADER] != malicious


async def test_unknown_route_uses_error_envelope(client: AsyncClient) -> None:
    response = await client.get("/rota-inexistente")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "http_error"


async def test_domain_error_uses_error_envelope(app: FastAPI, client: AsyncClient) -> None:
    @app.get("/_erro-de-dominio")
    async def raise_domain_error() -> None:
        raise NotFoundError("Execucao nao encontrada.", details={"execution_id": "abc"})

    response = await client.get("/_erro-de-dominio")

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert error["message"] == "Execucao nao encontrada."
    assert error["details"] == {"execution_id": "abc"}
    assert error["correlation_id"] is not None
