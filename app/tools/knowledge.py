"""Ferramenta de consulta a base de conhecimento -- o lado de LEITURA do registro.

Existe por dois motivos. O primeiro e util: dar ao agente de automacao uma forma de
consultar a base sem passar pelo no de pesquisa inteiro. O segundo e estrutural: sem ao
menos uma ferramenta de leitura, a regra de escopo nunca seria exercitada nos dois
sentidos, e um bug que exigisse aprovacao para TUDO passaria despercebido -- a suite
ficaria verde e o sistema, inutilizavel.

A ferramenta nao reimplementa recuperacao: ela adapta o `Retriever` que ja existe.
"""

from pydantic import BaseModel, Field

from app.rag.retriever import Retriever
from app.tools.base import ToolResult, ToolScope

#: Espelha `rag_top_k` em `Settings`, mas como teto, nao como padrao: aqui o valor vem de
#: um LLM, e um `top_k` alucinado em 500 custaria uma consulta enorme ao banco vetorial.
MAX_TOP_K = 20


class SearchKnowledgeInput(BaseModel):
    """Argumentos da busca."""

    query: str = Field(min_length=3, max_length=1_000, description="O que procurar na base.")
    top_k: int = Field(default=5, ge=1, le=MAX_TOP_K)


class SearchKnowledgeTool:
    """Procura trechos relevantes nos documentos ja indexados."""

    def __init__(self, retriever: Retriever) -> None:
        self._retriever = retriever

    @property
    def name(self) -> str:
        return "search_knowledge_base"

    @property
    def description(self) -> str:
        return (
            "Procura trechos relevantes nos documentos indexados e devolve as fontes. "
            "Use antes de afirmar qualquer coisa sobre o conteudo da base."
        )

    @property
    def scope(self) -> ToolScope:
        return ToolScope.READ

    @property
    def input_model(self) -> type[SearchKnowledgeInput]:
        return SearchKnowledgeInput

    async def run(self, payload: SearchKnowledgeInput) -> ToolResult:
        recuperado = await self._retriever.retrieve(payload.query, top_k=payload.top_k)

        # Base sem cobertura NAO e erro. E a resposta honesta, e precisa chegar assim ao
        # agente -- mesma regra do no de pesquisa (V2). Uma excecao aqui empurraria o
        # grafo para o caminho de degradacao por um motivo que nao e falha.
        if not recuperado.has_context:
            return ToolResult(
                tool=self.name,
                summary="A base de conhecimento nao cobre esta consulta.",
                output={
                    "found": 0,
                    "discarded": len(recuperado.discarded),
                    "min_score": recuperado.min_score,
                    "hits": [],
                },
            )

        return ToolResult(
            tool=self.name,
            summary=f"{len(recuperado.hits)} trecho(s) relevante(s) encontrado(s).",
            output={
                "found": len(recuperado.hits),
                "discarded": len(recuperado.discarded),
                "min_score": recuperado.min_score,
                "best_score": round(recuperado.best_score, 4),
                "hits": [
                    {
                        "document_id": hit.document_id,
                        "filename": hit.metadata.get("filename"),
                        "score": round(hit.score, 4),
                        "excerpt": hit.text[:300],
                    }
                    for hit in recuperado.hits
                ],
            },
        )
