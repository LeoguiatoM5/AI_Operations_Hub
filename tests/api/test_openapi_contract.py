"""Testes de contrato da especificacao OpenAPI.

Os tratadores garantem que toda falha saia no envelope `ErrorResponse` -- mas isso e
comportamento de runtime. Se o OpenAPI documentar outro formato, quem gerar um cliente
a partir da spec vai tratar erro no formato errado, e a falha so aparece em producao.

Defeito real encontrado assim: `/executions` documentava o `422` como
`{"detail": [...]}`, o padrao do FastAPI, enquanto o runtime devolvia o envelope.
"""

from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient

ERROR_STATUSES = {"400", "404", "409", "422", "429", "500", "502", "504"}


def _schema_ref(response: dict[str, Any]) -> str | None:
    content = response.get("content", {}).get("application/json", {})
    return str(content.get("schema", {}).get("$ref", "")) or None


def test_every_error_response_uses_the_error_envelope(app: FastAPI) -> None:
    spec = app.openapi()
    divergentes: list[str] = []

    for path, operations in spec["paths"].items():
        for method, operation in operations.items():
            for code, response in operation.get("responses", {}).items():
                if code not in ERROR_STATUSES:
                    continue
                if _schema_ref(response) != "#/components/schemas/ErrorResponse":
                    divergentes.append(f"{method.upper()} {path} -> {code}")

    assert divergentes == [], f"Respostas de erro fora do envelope: {divergentes}"


def test_every_endpoint_documents_validation_errors(app: FastAPI) -> None:
    spec = app.openapi()

    for path, operations in spec["paths"].items():
        for method, operation in operations.items():
            assert "422" in operation["responses"], f"{method.upper()} {path} sem 422"


async def test_documented_shape_matches_the_actual_response(client: AsyncClient) -> None:
    """A spec e o runtime precisam concordar -- e o unico jeito de saber e comparar."""
    response = await client.get("/executions", params={"limit": 0})

    assert response.status_code == 422
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "details", "correlation_id"}
