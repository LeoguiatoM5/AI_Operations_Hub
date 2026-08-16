"""Testes do endpoint POST /chat.

Cobrem o caminho feliz e os modos de falha que importam num sistema de IA: modelo
respondendo fora do formato, provedor fora do ar, cota estourada e entrada invalida.
"""

import json
from collections.abc import Callable

import pytest
from httpx import AsyncClient

from app.api.middleware import CORRELATION_ID_HEADER
from app.llm.base import LLMProvider
from app.llm.exceptions import LLMAuthenticationError, LLMRateLimitError, LLMTimeoutError
from app.llm.fake_provider import FakeLLMProvider
from tests.conftest import triage_json

PEDIDO = {"message": "Analise os chamados criticos de hoje e gere um relatorio."}


# ---------------------------------------------------------------- caminho feliz


async def test_returns_the_full_execution(client: AsyncClient) -> None:
    response = await client.post("/chat", json=PEDIDO)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "completed"
    assert body["execution_id"]
    assert body["result"]["intent"] == "analise"
    assert body["result"]["urgency"] == "alta"


async def test_records_the_agent_chain(client: AsyncClient) -> None:
    body = (await client.post("/chat", json=PEDIDO)).json()

    assert len(body["steps"]) == 1
    step = body["steps"][0]
    assert step["sequence"] == 1
    assert step["agent"] == "triage"
    assert step["action"] == "classify_request"
    assert step["status"] == "completed"
    assert step["provider"] == "fake"


async def test_reports_usage_and_cost(client: AsyncClient) -> None:
    body = (await client.post("/chat", json=PEDIDO)).json()

    assert body["usage"]["total_tokens"] > 0
    assert body["usage"]["total_tokens"] == (
        body["usage"]["prompt_tokens"] + body["usage"]["completion_tokens"]
    )
    assert body["usage"]["cost_usd"] >= 0
    assert body["duration_ms"] is not None


async def test_correlation_id_links_request_to_execution(client: AsyncClient) -> None:
    """O mesmo identificador aparece nos logs e no registro: e assim que se rastreia."""
    response = await client.post("/chat", json=PEDIDO, headers={CORRELATION_ID_HEADER: "pedido-42"})

    assert response.json()["correlation_id"] == "pedido-42"


async def test_execution_is_retrievable_afterwards(client: AsyncClient) -> None:
    execution_id = (await client.post("/chat", json=PEDIDO)).json()["execution_id"]

    detail = await client.get(f"/executions/{execution_id}")

    assert detail.status_code == 200
    assert detail.json()["execution_id"] == execution_id
    assert len(detail.json()["steps"]) == 1


# ---------------------------------------------------------------- formato invalido


async def test_recovers_when_the_model_fixes_its_own_output(
    make_client: Callable[[LLMProvider], AsyncClient],
) -> None:
    """Retry dirigido: o erro de validacao volta para o modelo, que corrige."""
    provider = FakeLLMProvider(script=["isto nao e json", triage_json()])
    client = make_client(provider)

    response = await client.post("/chat", json=PEDIDO)

    assert response.status_code == 201
    assert provider.call_count == 2
    assert response.json()["status"] == "completed"


async def test_repair_attempt_is_charged(
    make_client: Callable[[LLMProvider], AsyncClient],
) -> None:
    """Duas chamadas custam o dobro: reportar so a ultima esconderia metade do custo."""
    uma_chamada = make_client(FakeLLMProvider(script=[triage_json()]))
    duas_chamadas = make_client(FakeLLMProvider(script=["quebrado", triage_json()]))

    barato = (await uma_chamada.post("/chat", json=PEDIDO)).json()
    caro = (await duas_chamadas.post("/chat", json=PEDIDO)).json()

    assert caro["usage"]["total_tokens"] > barato["usage"]["total_tokens"]


async def test_gives_up_when_the_model_never_produces_valid_json(
    make_client: Callable[[LLMProvider], AsyncClient],
) -> None:
    client = make_client(FakeLLMProvider(script=["nunca vai ser json"]))

    response = await client.post("/chat", json=PEDIDO)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "llm_response_format_error"


async def test_rejects_json_that_violates_the_schema(
    make_client: Callable[[LLMProvider], AsyncClient],
) -> None:
    """JSON bem formado nao basta: `urgency` fora do enum e `confidence` acima de 1."""
    invalido = json.dumps({"intent": "analise", "urgency": "urgentissimo", "confidence": 7.5})
    client = make_client(FakeLLMProvider(script=[invalido]))

    response = await client.post("/chat", json=PEDIDO)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "llm_response_format_error"


async def test_accepts_json_wrapped_in_markdown_fences(
    make_client: Callable[[LLMProvider], AsyncClient],
) -> None:
    """O modelo adiciona cercas de codigo mesmo quando o modo JSON foi pedido."""
    client = make_client(FakeLLMProvider(script=[f"```json\n{triage_json()}\n```"]))

    response = await client.post("/chat", json=PEDIDO)

    assert response.status_code == 201


# ---------------------------------------------------------------- provedor indisponivel


@pytest.mark.parametrize(
    ("erro", "status_esperado", "codigo_esperado"),
    [
        (LLMTimeoutError(), 504, "llm_timeout"),
        (LLMRateLimitError(), 429, "llm_rate_limit"),
        (LLMAuthenticationError(), 500, "llm_authentication_error"),
    ],
)
async def test_provider_failure_maps_to_an_honest_http_status(
    make_client: Callable[[LLMProvider], AsyncClient],
    erro: Exception,
    status_esperado: int,
    codigo_esperado: str,
) -> None:
    """Falha de LLM nao vira 200 com status interno: monitoramento precisa enxergar."""
    client = make_client(FakeLLMProvider(script=[erro]))

    response = await client.post("/chat", json=PEDIDO)

    assert response.status_code == status_esperado
    assert response.json()["error"]["code"] == codigo_esperado


async def test_failure_is_recorded_and_traceable(
    make_client: Callable[[LLMProvider], AsyncClient],
) -> None:
    """O erro carrega o execution_id: a falha fica investigavel, nao se perde."""
    client = make_client(FakeLLMProvider(script=[LLMTimeoutError()]))

    response = await client.post("/chat", json=PEDIDO)
    execution_id = response.json()["error"]["details"]["execution_id"]

    detail = (await client.get(f"/executions/{execution_id}")).json()

    assert detail["status"] == "failed"
    assert detail["error_code"] == "llm_timeout"
    assert detail["steps"][0]["status"] == "failed"
    assert detail["steps"][0]["error_code"] == "llm_timeout"


# ---------------------------------------------------------------- entrada invalida


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"message": ""},
        {"message": "ab"},
        {"message": "x" * 8_001},
        {"mensagem": "campo com o nome errado"},
    ],
)
async def test_rejects_invalid_payloads(client: AsyncClient, payload: dict[str, str]) -> None:
    response = await client.post("/chat", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_oversized_message_never_reaches_the_model(
    make_client: Callable[[LLMProvider], AsyncClient],
) -> None:
    """A validacao acontece antes da chamada paga: entrada gigante nao gasta tokens."""
    provider = FakeLLMProvider(script=[triage_json()])
    client = make_client(provider)

    await client.post("/chat", json={"message": "x" * 20_000})

    assert provider.call_count == 0
