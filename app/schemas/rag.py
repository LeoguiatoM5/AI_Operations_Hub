"""Schemas do endpoint de consulta a base de conhecimento."""

from typing import Self

from pydantic import BaseModel, Field

from app.rag.base import SearchHit
from app.schemas.execution import UsageSummary
from app.services.rag_service import RagResult

MAX_QUESTION_LENGTH = 2_000


class RagQueryRequest(BaseModel):
    """Pergunta a base de conhecimento."""

    question: str = Field(
        min_length=3,
        max_length=MAX_QUESTION_LENGTH,
        description="Pergunta em linguagem natural.",
        examples=["Qual o prazo para solicitar reembolso de despesas?"],
    )
    top_k: int | None = Field(
        default=None, ge=1, le=20, description="Quantos trechos recuperar. Padrao: RAG_TOP_K."
    )
    document_ids: list[str] | None = Field(
        default=None,
        max_length=50,
        description="Restringe a busca a documentos especificos.",
    )


class SourceResponse(BaseModel):
    """Um trecho usado como fonte."""

    number: int = Field(description="Numero citado na resposta.")
    document_id: str
    filename: str | None = None
    score: float
    excerpt: str = Field(description="Inicio do trecho, para conferencia rapida.")
    cited: bool = Field(description="Se a resposta de fato se apoiou neste trecho.")

    @classmethod
    def from_hit(cls, hit: SearchHit, *, number: int, cited: bool) -> Self:
        nome = hit.metadata.get("filename")
        return cls(
            number=number,
            document_id=hit.document_id,
            filename=str(nome) if nome is not None else None,
            score=round(hit.score, 4),
            excerpt=hit.text[:300],
            cited=cited,
        )


class RagQueryResponse(BaseModel):
    """Resposta ancorada, com as fontes que a sustentam."""

    question: str
    answered: bool = Field(
        description="False quando a base nao cobre a pergunta. E uma resposta valida."
    )
    answer: str
    confidence: float | None = None
    sources: list[SourceResponse] = Field(default_factory=list)
    retrieval: "RetrievalSummary"
    usage: UsageSummary
    latency_ms: float
    repairs: int = Field(description="Tentativas de correcao do formato ou das citacoes.")

    @classmethod
    def from_result(cls, result: RagResult) -> Self:
        citados = {hit.chunk_id for hit in result.cited_sources}
        return cls(
            question=result.question,
            answered=result.answered,
            answer=(
                result.answer.answer
                if result.answer is not None
                else "A base de conhecimento nao contem informacao suficiente para "
                "responder a esta pergunta."
            ),
            confidence=result.answer.confidence if result.answer is not None else None,
            sources=[
                SourceResponse.from_hit(hit, number=posicao, cited=hit.chunk_id in citados)
                for posicao, hit in enumerate(result.sources, start=1)
            ],
            retrieval=RetrievalSummary(
                chunks_retrieved=len(result.sources),
                chunks_cited=len(result.cited_sources),
                min_score=round(result.min_score, 4),
                best_score=round(result.best_score, 4),
            ),
            usage=UsageSummary(
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                total_tokens=result.prompt_tokens + result.completion_tokens,
                cost_usd=result.cost_usd,
            ),
            latency_ms=round(result.latency_ms, 3),
            repairs=result.repairs,
        )


class RetrievalSummary(BaseModel):
    """O que a busca encontrou, antes e depois do corte de relevancia."""

    chunks_retrieved: int
    chunks_cited: int
    min_score: float = Field(description="Corte de relevancia aplicado.")
    best_score: float


RagQueryResponse.model_rebuild()
