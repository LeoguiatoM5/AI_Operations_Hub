"""Hierarquia de erros do dominio.

Toda falha esperada herda de AIHubError e carrega um codigo estavel e um status HTTP.
Isso permite um unico tratador na borda da API, com resposta em formato consistente.
"""

from typing import Any, ClassVar


class AIHubError(Exception):
    """Erro base do dominio."""

    code: ClassVar[str] = "internal_error"
    http_status: ClassVar[int] = 500
    default_message: ClassVar[str] = "Erro interno inesperado."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.default_message
        self.details = details or {}
        super().__init__(self.message)


class ConfigurationError(AIHubError):
    """Configuracao ausente ou invalida."""

    code = "configuration_error"
    http_status = 500
    default_message = "Configuracao invalida."


class ValidationError(AIHubError):
    """Entrada rejeitada por regra de negocio (nao por schema)."""

    code = "validation_error"
    http_status = 422
    default_message = "Dados de entrada invalidos."


class NotFoundError(AIHubError):
    """Recurso solicitado nao existe."""

    code = "not_found"
    http_status = 404
    default_message = "Recurso nao encontrado."
