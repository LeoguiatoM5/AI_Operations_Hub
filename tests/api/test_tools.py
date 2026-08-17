"""Testes do endpoint de catalogo de ferramentas."""

from httpx import AsyncClient


async def test_lists_the_available_tools(client: AsyncClient) -> None:
    response = await client.get("/tools")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == len(body["tools"])
    assert {tool["name"] for tool in body["tools"]} == {
        "search_knowledge_base",
        "send_notification",
    }


async def test_marks_write_tools_as_requiring_approval(client: AsyncClient) -> None:
    """O contrato publico precisa dizer o que exige humano: quem integra com a API
    decide a interface a partir disso."""
    body = (await client.get("/tools")).json()
    por_nome = {tool["name"]: tool for tool in body["tools"]}

    assert por_nome["send_notification"]["scope"] == "write"
    assert por_nome["send_notification"]["requires_approval"] is True
    assert por_nome["search_knowledge_base"]["scope"] == "read"
    assert por_nome["search_knowledge_base"]["requires_approval"] is False
    assert body["write_tools"] == ["send_notification"]


async def test_publishes_the_input_schema_of_each_tool(client: AsyncClient) -> None:
    """O schema e o que permite a um cliente (ou ao servidor MCP do V6) montar a chamada
    sem ler o codigo."""
    body = (await client.get("/tools")).json()
    por_nome = {tool["name"]: tool for tool in body["tools"]}

    propriedades = por_nome["send_notification"]["input_schema"]["properties"]

    assert set(propriedades) == {"title", "body", "channel"}


async def test_there_is_no_endpoint_to_execute_a_tool(client: AsyncClient) -> None:
    """Uma rota de execucao direta seria um atalho para disparar acao de escrita sem
    passar pelo fluxo de aprovacao -- exatamente o que o V4 existe para impedir."""
    response = await client.post("/tools/send_notification/execute", json={})

    assert response.status_code == 404
