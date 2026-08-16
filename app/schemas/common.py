"""Schemas compartilhados entre endpoints."""

from pydantic import BaseModel, Field


class ErrorBody(BaseModel):
    """Conteudo de um erro retornado pela API."""

    code: str = Field(description="Codigo estavel do erro, adequado para tratamento programatico.")
    message: str = Field(description="Mensagem legivel por humanos.")
    details: dict[str, object] = Field(default_factory=dict, description="Contexto adicional.")
    correlation_id: str | None = Field(
        default=None, description="Identificador do request, util para rastrear nos logs."
    )


class ErrorResponse(BaseModel):
    """Envelope unico de erro da API."""

    error: ErrorBody
