"""Erros da camada de ferramentas.

Tres falhas distintas, com codigos distintos, porque o diagnostico e diferente em cada
caso: o plano pediu uma ferramenta que nao existe (problema de prompt ou de catalogo),
os argumentos nao passaram na validacao (problema de geracao), ou o sistema externo
falhou (problema de infraestrutura, e o unico dos tres que vale repetir).
"""

from app.core.exceptions import AIHubError, NotFoundError, ValidationError


class ToolNotFoundError(NotFoundError):
    """O nome pedido nao esta no registro."""

    code = "tool_not_found"
    default_message = "Ferramenta desconhecida."


class ToolInputError(ValidationError):
    """Os argumentos nao satisfazem o schema declarado pela ferramenta."""

    code = "tool_input_invalid"
    default_message = "Argumentos invalidos para a ferramenta."


class ToolExecutionError(AIHubError):
    """A ferramenta foi chamada corretamente e o sistema externo falhou."""

    code = "tool_execution_failed"
    http_status = 502
    default_message = "A ferramenta falhou ao executar."
