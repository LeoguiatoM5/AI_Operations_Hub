"""Respostas de erro declaradas no OpenAPI.

Os tratadores em `app/api/errors.py` garantem que TODA falha saia no envelope
`ErrorResponse`. Sem declarar isso aqui, porem, o FastAPI documenta o formato padrao
dele (`{"detail": [...]}`) -- e a spec passa a mentir sobre o contrato.

Isso importa mais do que parece: quem gera um cliente a partir do OpenAPI produz codigo
que trata o erro no formato errado, e a falha so aparece em producao.
"""

from typing import Any

from app.schemas.common import ErrorResponse

#: Erros possiveis em qualquer endpoint.
COMMON_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    422: {"model": ErrorResponse, "description": "Payload ou parametros invalidos"},
    500: {"model": ErrorResponse, "description": "Erro interno inesperado"},
}


def with_errors(**extra: dict[str, Any]) -> dict[int | str, dict[str, Any]]:
    """Combina os erros comuns com os especificos de um endpoint."""
    return {**COMMON_ERROR_RESPONSES, **{int(code): body for code, body in extra.items()}}
