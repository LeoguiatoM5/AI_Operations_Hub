"""Recuperacao de contexto.

Junta o provedor de embedding e o banco vetorial numa operacao so, e aplica o corte de
relevancia -- a parte do RAG que decide quando a resposta honesta e "a base nao cobre
esta pergunta".
"""

from dataclasses import dataclass, field

from app.core.logging import get_logger
from app.rag.base import EmbeddingProvider, SearchHit, VectorStore

logger = get_logger(__name__)


@dataclass(frozen=True)
class RetrievalResult:
    """Trechos recuperados e o que foi descartado no caminho."""

    hits: list[SearchHit] = field(default_factory=list)
    #: Trechos encontrados mas cortados por baixa similaridade. Guardados porque a
    #: diferenca entre "nao ha nada na base" e "ha algo, mas fraco" muda o diagnostico.
    discarded: list[SearchHit] = field(default_factory=list)
    min_score: float = 0.0
    embedding_tokens: int = 0
    embedding_cost_usd: float = 0.0

    @property
    def has_context(self) -> bool:
        return bool(self.hits)

    @property
    def best_score(self) -> float:
        return self.hits[0].score if self.hits else 0.0


class Retriever:
    """Transforma uma pergunta nos trechos mais relevantes da base."""

    def __init__(
        self,
        embedder: EmbeddingProvider,
        store: VectorStore,
        *,
        top_k: int = 5,
        min_score: float | None = None,
    ) -> None:
        """
        Args:
            min_score: sobrescreve o piso declarado pelo provedor. `None` usa o do
                provedor, que e o comportamento correto na maioria dos casos -- a escala
                de similaridade depende do modelo de embedding.
        """
        self._embedder = embedder
        self._store = store
        self._top_k = top_k
        self._min_score = min_score

    @property
    def min_score(self) -> float:
        return self._min_score if self._min_score is not None else self._embedder.min_relevant_score

    async def retrieve(
        self, question: str, *, top_k: int | None = None, document_ids: list[str] | None = None
    ) -> RetrievalResult:
        embedding = await self._embedder.embed_query(question)
        encontrados = await self._store.search(
            embedding.vectors[0],
            top_k=top_k or self._top_k,
            document_ids=document_ids,
        )

        limite = self.min_score
        relevantes = [hit for hit in encontrados if hit.score >= limite]
        descartados = [hit for hit in encontrados if hit.score < limite]

        logger.info(
            "retrieval_completed",
            question_length=len(question),
            found=len(encontrados),
            kept=len(relevantes),
            discarded=len(descartados),
            min_score=limite,
            best_score=relevantes[0].score if relevantes else None,
            embedding_model=self._embedder.model,
        )

        return RetrievalResult(
            hits=relevantes,
            discarded=descartados,
            min_score=limite,
            embedding_tokens=embedding.usage.tokens,
            embedding_cost_usd=embedding.usage.cost_usd,
        )
