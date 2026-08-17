"""Servidor MCP do AI Operations Hub.

MCP (Model Context Protocol) e um protocolo para um cliente de LLM -- Claude Desktop, um
IDE, um agente de terceiros -- descobrir e chamar ferramentas de um sistema externo. Onde
uma API REST publica recursos para um programa que ja sabe o que quer, o MCP publica
capacidades para um modelo que precisa descobrir o que existe.

**O ponto arquitetural do V6, e ele nao custou nada:** este arquivo nao contem regra de
negocio. Ele chama `RagService`, `WorkflowService` e os repositorios -- exatamente os
mesmos que `app/api/routes/` chama. REST e MCP sao dois adaptadores sobre uma camada de
servico que nunca soube que HTTP existia. A camada `services/` foi escrita assim desde o
V1 (invariante 1 do roadmap), e este servidor e a cobranca dessa promessa.

## A fronteira que este servidor NAO atravessa

`POST /approvals/{id}/approve` **nao tem ferramenta MCP correspondente**, e a ausencia e
o recurso mais importante daqui. Ver `list_pending_approvals`.
"""

from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from app.core.exceptions import AIHubError
from app.core.logging import get_logger
from app.models.enums import ApprovalStatus
from mcp_server.container import ServiceContainer

logger = get_logger(__name__)

INSTRUCTIONS = """\
Ferramentas do AI Operations Hub: base de conhecimento corporativa, execucao de \
workflows multiagente e consulta ao rastro de execucoes.

Duas regras que valem para todas as ferramentas:

1. As respostas da base sao fundamentadas em documentos e trazem as fontes. Quando a base \
nao cobre um assunto, a resposta diz isso -- e essa e a resposta correta, nao uma falha.
2. Acoes que escrevem em sistemas externos NAO sao executadas por estas ferramentas. Elas \
param aguardando decisao de uma pessoa, que so pode ser dada fora daqui.\
"""

#: Teto de itens por listagem. Uma janela de contexto e cara: devolver duzentas execucoes
#: para um modelo que queria ver as ultimas cinco gasta tokens de quem chamou.
MAX_ITEMS = 50


def build_server(container: ServiceContainer, *, name: str = "ai-operations-hub") -> FastMCP:
    """Monta o servidor com as ferramentas ligadas aos servicos."""
    mcp = FastMCP(name, instructions=INSTRUCTIONS)

    # ------------------------------------------------------------------ leitura

    @mcp.tool()
    async def search_knowledge_base(
        question: str = Field(description="Pergunta em linguagem natural.", max_length=1000),
        top_k: int = Field(default=5, ge=1, le=20, description="Quantos trechos recuperar."),
    ) -> dict[str, Any]:
        """Responde uma pergunta usando a base de conhecimento da empresa.

        A resposta e fundamentada exclusivamente nos documentos indexados e vem com as
        fontes citadas. Se `answered` for `false`, a base nao cobre o assunto -- e essa e
        a resposta correta, nao um erro. Nao complete com conhecimento proprio nesse caso.
        """
        async with container.session() as session:
            resultado = await container.rag_service(session).query(question, top_k=top_k)

        return {
            "answered": resultado.answered,
            "answer": resultado.answer.answer if resultado.answer else None,
            "confidence": resultado.answer.confidence if resultado.answer else None,
            "sources": [
                {
                    "document": hit.metadata.get("filename", hit.document_id),
                    "excerpt": hit.text[:500],
                    "similarity": round(hit.score, 4),
                }
                for hit in resultado.cited_sources
            ],
            "cost_usd": resultado.cost_usd,
        }

    @mcp.tool()
    async def list_documents(
        limit: int = Field(default=20, ge=1, le=MAX_ITEMS),
    ) -> dict[str, Any]:
        """Lista os documentos indexados na base de conhecimento.

        Util antes de perguntar: mostra quais assuntos a base cobre.
        """
        async with container.session() as session:
            repositorio = container.documents(session)
            documentos = await repositorio.list(limit=limit)
            total = await repositorio.count()
            trechos = await repositorio.total_chunks()

        return {
            "total": total,
            "indexed_chunks": trechos,
            "documents": [
                {
                    "id": item.id,
                    "filename": item.filename,
                    "status": item.status.value,
                    "chunks": item.chunk_count,
                    "indexed_at": item.indexed_at.isoformat() if item.indexed_at else None,
                }
                for item in documentos
            ],
        }

    @mcp.tool()
    async def get_execution(
        execution_id: str = Field(description="Identificador devolvido por run_workflow."),
    ) -> dict[str, Any]:
        """Mostra uma execucao com a cadeia completa de agentes.

        E aqui que se responde "por que o sistema concluiu isso?": cada passo traz o
        agente, o que ele produziu, quantos tokens custou e se precisou repetir.
        """
        async with container.session() as session:
            execucao = await container.executions(session).get(execution_id)
            if execucao is None:
                return {"found": False, "execution_id": execution_id}

            return {
                "found": True,
                "id": execucao.id,
                "status": execucao.status.value,
                "request": execucao.request_text,
                "result": execucao.result,
                "quality_score": execucao.quality_score,
                "total_tokens": execucao.total_tokens,
                "cost_usd": execucao.total_cost_usd,
                "duration_ms": execucao.duration_ms,
                "steps": [
                    {
                        "sequence": passo.sequence,
                        "agent": passo.agent,
                        "action": passo.action,
                        "status": passo.status.value,
                        "attempts": passo.attempts,
                        "cost_usd": passo.cost_usd,
                        "error": passo.error_message,
                    }
                    for passo in execucao.agent_executions
                ],
            }

    @mcp.tool()
    async def list_pending_approvals(
        limit: int = Field(default=20, ge=1, le=MAX_ITEMS),
    ) -> dict[str, Any]:
        """Lista acoes de escrita paradas esperando decisao de uma pessoa.

        **Nao existe ferramenta para aprovar ou recusar, e isso e deliberado.** A decisao
        precisa vir de uma pessoa, pela interface humana do sistema
        (`POST /approvals/{id}/approve`). Um cliente MCP e um modelo de linguagem: dar a
        ele o poder de aprovar significaria a IA autorizando a propria acao, que e
        exatamente o que a aprovacao humana existe para impedir.

        Use esta ferramenta para RELATAR o que esta pendente a quem puder decidir.
        """
        async with container.session() as session:
            repositorio = container.approvals(session)
            pendentes = await repositorio.list(limit=limit, status=ApprovalStatus.PENDING)
            total = await repositorio.count(status=ApprovalStatus.PENDING)

        return {
            "total_pending": total,
            "decide_at": "POST /approvals/{id}/approve  (interface humana, fora do MCP)",
            "approvals": [
                {
                    "id": item.id,
                    "execution_id": item.execution_id,
                    "tool": item.tool,
                    "arguments": item.arguments,
                    "reason": item.reason,
                    "created_at": item.created_at.isoformat(),
                }
                for item in pendentes
            ],
        }

    # ------------------------------------------------------------------ execucao

    @mcp.tool()
    async def run_workflow(
        task: str = Field(
            description="O que deve ser feito, em linguagem natural.", max_length=8000
        ),
        data: Any = Field(default=None, description="Dados estruturados a analisar, se houver."),
    ) -> dict[str, Any]:
        """Executa o workflow multiagente: planeja, pesquisa, analisa e escreve o relatorio.

        Custa varias chamadas de LLM. Para uma pergunta simples sobre a base, prefira
        `search_knowledge_base`.

        Se o plano envolver uma acao que escreve em sistema externo, a execucao **para** em
        `waiting_approval` e nada e executado. O retorno traz a acao pretendida; a decisao
        cabe a uma pessoa, fora deste servidor.
        """
        async with container.session() as session:
            execucao, estado, aprovacao = await container.workflow_service(session).run(
                task=task, data=data
            )

            resposta: dict[str, Any] = {
                "execution_id": execucao.id,
                "status": execucao.status.value,
                "agents_executed": list(estado.get("completed", [])),
                "report": estado.get("report"),
                "research": estado.get("research"),
                "analysis": estado.get("analysis"),
                "quality": estado.get("quality"),
                "errors": estado.get("errors", []),
                "cost_usd": execucao.total_cost_usd,
            }
            if aprovacao is not None:
                resposta["pending_approval"] = {
                    "id": aprovacao.id,
                    "tool": aprovacao.tool,
                    "arguments": aprovacao.arguments,
                    "reason": aprovacao.reason,
                    "note": (
                        "Nada foi executado. Uma pessoa precisa decidir em "
                        "POST /approvals/{id}/approve -- nao ha ferramenta MCP para isso."
                    ),
                }
            return resposta

    logger.info("mcp_server_built", name=name)
    return mcp


async def describe_tools(mcp: FastMCP) -> list[dict[str, str]]:
    """Catalogo do servidor, para inspecao e teste."""
    return [
        {"name": item.name, "description": (item.description or "").split("\n")[0]}
        for item in await mcp.list_tools()
    ]


__all__ = ["AIHubError", "build_server", "describe_tools"]
