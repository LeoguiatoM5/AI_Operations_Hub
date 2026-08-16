"""Consulta a base de conhecimento com resposta ancorada em fontes.

Fluxo: pergunta -> vetor -> busca -> corte de relevancia -> LLM restrito ao contexto ->
resposta com citacoes verificaveis.

Duas guardas que existem porque o modo de falha delas e SILENCIOSO:

- base com modelos de embedding misturados devolve resultados sem sentido, sem erro;
- contexto insuficiente convida o modelo a completar com conhecimento proprio, e a
  resposta parece boa.
"""

from dataclasses import dataclass

from app.agents.research import ResearchAgent, ResearchAnswer
from app.core.exceptions import AIHubError
from app.core.logging import get_logger
from app.rag.base import SearchHit
from app.rag.retriever import Retriever
from app.repositories.document_repository import DocumentRepository

logger = get_logger(__name__)


class EmbeddingModelMismatchError(AIHubError):
    """A base contem documentos indexados com outro modelo de embedding."""

    code = "embedding_model_mismatch"
    http_status = 409
    default_message = (
        "A base contem documentos indexados com outro modelo de embedding. "
        "Vetores de modelos diferentes nao sao comparaveis: reindexe os documentos."
    )


@dataclass(frozen=True)
class RagResult:
    """Resposta e tudo que foi preciso para produzi-la."""

    question: str
    answer: ResearchAnswer | None
    sources: list[SearchHit]
    cited_sources: list[SearchHit]
    min_score: float
    best_score: float
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    latency_ms: float
    repairs: int

    @property
    def answered(self) -> bool:
        return self.answer is not None and self.answer.answered


class RagService:
    """Responde perguntas sobre a base de conhecimento."""

    def __init__(
        self,
        retriever: Retriever,
        agent: ResearchAgent,
        documents: DocumentRepository,
        *,
        embedding_model: str,
    ) -> None:
        self._retriever = retriever
        self._agent = agent
        self._documents = documents
        self._embedding_model = embedding_model

    async def query(
        self,
        question: str,
        *,
        top_k: int | None = None,
        document_ids: list[str] | None = None,
    ) -> RagResult:
        """Recupera contexto e responde, ou informa que a base nao cobre a pergunta.

        Raises:
            EmbeddingModelMismatchError: a base foi indexada com outro modelo.
        """
        await self._ensure_index_is_comparable()

        retrieval = await self._retriever.retrieve(question, top_k=top_k, document_ids=document_ids)

        if not retrieval.has_context:
            # Chamar o LLM sem contexto seria convidar a alucinacao: o modelo responderia
            # com conhecimento proprio, e a resposta pareceria tao boa quanto uma
            # fundamentada. Nao gastamos a chamada.
            logger.info(
                "rag_without_context",
                discarded=len(retrieval.discarded),
                best_discarded_score=(
                    retrieval.discarded[0].score if retrieval.discarded else None
                ),
                min_score=retrieval.min_score,
            )
            return RagResult(
                question=question,
                answer=None,
                sources=[],
                cited_sources=[],
                min_score=retrieval.min_score,
                best_score=0.0,
                prompt_tokens=retrieval.embedding_tokens,
                completion_tokens=0,
                cost_usd=retrieval.embedding_cost_usd,
                latency_ms=0.0,
                repairs=0,
            )

        outcome = await self._agent.run(question, retrieval.hits)
        resposta = outcome.payload

        # As citacoes ja foram validadas contra o intervalo valido pelo schema dinamico;
        # aqui apenas as resolvemos para os trechos correspondentes.
        citadas = [retrieval.hits[numero - 1] for numero in resposta.citations]

        return RagResult(
            question=question,
            answer=resposta,
            sources=retrieval.hits,
            cited_sources=citadas,
            min_score=retrieval.min_score,
            best_score=retrieval.best_score,
            prompt_tokens=outcome.response.usage.prompt_tokens + retrieval.embedding_tokens,
            completion_tokens=outcome.response.usage.completion_tokens,
            cost_usd=round(outcome.response.cost_usd + retrieval.embedding_cost_usd, 8),
            latency_ms=outcome.response.latency_ms,
            repairs=outcome.repairs,
        )

    async def _ensure_index_is_comparable(self) -> None:
        """Recusa a consulta se a base tiver sido indexada com outro modelo.

        Vetores de modelos diferentes ocupam espacos diferentes: comparar uns com os
        outros produz similaridades sem significado. O sistema nao teria como perceber --
        os numeros continuam entre 0 e 1, a busca continua devolvendo resultados, e eles
        sao aleatorios.
        """
        modelos = await self._documents.distinct_embedding_models()
        incompativeis = modelos - {self._embedding_model}
        if incompativeis:
            raise EmbeddingModelMismatchError(
                details={
                    "current_model": self._embedding_model,
                    "models_in_index": sorted(modelos),
                    "hint": "Remova e reenvie os documentos, ou volte ao modelo anterior.",
                }
            )
