"""Testes dos endpoints de consulta ao historico."""

from httpx import AsyncClient

PEDIDO = {"message": "Analise os chamados criticos de hoje."}


async def test_empty_history(client: AsyncClient) -> None:
    response = await client.get("/executions")

    assert response.status_code == 200
    assert response.json() == {"total": 0, "limit": 50, "offset": 0, "items": []}


async def test_lists_executions_most_recent_first(client: AsyncClient) -> None:
    for index in range(3):
        await client.post("/chat", json={"message": f"Solicitacao numero {index}."})

    body = (await client.get("/executions")).json()

    assert body["total"] == 3
    assert len(body["items"]) == 3
    assert body["items"][0]["request_text"] == "Solicitacao numero 2."


async def test_summary_omits_the_agent_chain(client: AsyncClient) -> None:
    """Listagem nao carrega os passos: e o que mantem a consulta barata."""
    await client.post("/chat", json=PEDIDO)

    item = (await client.get("/executions")).json()["items"][0]

    assert "steps" not in item
    assert "usage" in item


async def test_paginates(client: AsyncClient) -> None:
    for index in range(5):
        await client.post("/chat", json={"message": f"Solicitacao numero {index}."})

    primeira = (await client.get("/executions", params={"limit": 2, "offset": 0})).json()
    segunda = (await client.get("/executions", params={"limit": 2, "offset": 2})).json()

    assert primeira["total"] == 5
    assert len(primeira["items"]) == 2
    ids_primeira = {item["execution_id"] for item in primeira["items"]}
    ids_segunda = {item["execution_id"] for item in segunda["items"]}
    assert ids_primeira.isdisjoint(ids_segunda)


async def test_filters_by_status(client: AsyncClient) -> None:
    await client.post("/chat", json=PEDIDO)

    concluidas = (await client.get("/executions", params={"status": "completed"})).json()
    falhas = (await client.get("/executions", params={"status": "failed"})).json()

    assert concluidas["total"] == 1
    assert falhas["total"] == 0


async def test_rejects_invalid_status_filter(client: AsyncClient) -> None:
    response = await client.get("/executions", params={"status": "inventado"})

    assert response.status_code == 422


async def test_rejects_out_of_range_limit(client: AsyncClient) -> None:
    assert (await client.get("/executions", params={"limit": 0})).status_code == 422
    assert (await client.get("/executions", params={"limit": 500})).status_code == 422


async def test_detail_includes_the_agent_chain(client: AsyncClient) -> None:
    execution_id = (await client.post("/chat", json=PEDIDO)).json()["execution_id"]

    body = (await client.get(f"/executions/{execution_id}")).json()

    assert body["execution_id"] == execution_id
    assert body["result"]["intent"] == "analise"
    assert [step["agent"] for step in body["steps"]] == ["triage"]


async def test_unknown_execution_returns_404_with_context(client: AsyncClient) -> None:
    response = await client.get("/executions/naoexiste")

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert error["details"] == {"execution_id": "naoexiste"}
