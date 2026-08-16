"""Endpoint de consulta a base de conhecimento."""

from fastapi import APIRouter

from app.api.deps import RagServiceDep
from app.api.responses import with_errors
from app.schemas.common import ErrorResponse
from app.schemas.rag import RagQueryRequest, RagQueryResponse

router = APIRouter(prefix="/rag", tags=["rag"])


@router.post(
    "/query",
    response_model=RagQueryResponse,
    summary="Responde uma pergunta usando a base de conhecimento",
    responses=with_errors(
        **{
            "409": {
                "model": ErrorResponse,
                "description": "Base indexada com outro modelo de embedding",
            },
            "502": {"model": ErrorResponse, "description": "Falha do provedor de LLM"},
            "504": {"model": ErrorResponse, "description": "Provedor nao respondeu a tempo"},
        }
    ),
)
async def query_knowledge_base(
    payload: RagQueryRequest, service: RagServiceDep
) -> RagQueryResponse:
    """Recupera trechos relevantes e responde usando exclusivamente o que foi recuperado.

    `answered: false` significa que a base nao cobre a pergunta -- e uma resposta correta,
    nao um erro. Toda afirmacao da resposta e sustentada por um trecho listado em
    `sources` com `cited: true`.
    """
    result = await service.query(
        payload.question, top_k=payload.top_k, document_ids=payload.document_ids
    )
    return RagQueryResponse.from_result(result)
