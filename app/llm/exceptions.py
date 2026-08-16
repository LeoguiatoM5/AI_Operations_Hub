"""Erros da camada de LLM.

Cada SDK lanca as proprias excecoes. Traduzimos todas para esta hierarquia, para que
o codigo acima da abstracao nunca precise conhecer o provedor em uso.

O atributo `retryable` e o que a politica de retry consulta: nao adianta repetir uma
chamada rejeitada por chave invalida, mas vale repetir um timeout ou um 429.
"""

from typing import ClassVar

from app.core.exceptions import AIHubError


class LLMError(AIHubError):
    """Falha generica na comunicacao com o provedor de LLM."""

    code = "llm_error"
    http_status = 502
    default_message = "Falha ao consultar o provedor de LLM."
    retryable: ClassVar[bool] = True


class LLMTimeoutError(LLMError):
    """O provedor nao respondeu dentro do tempo configurado."""

    code = "llm_timeout"
    http_status = 504
    default_message = "O provedor de LLM nao respondeu a tempo."
    retryable = True


class LLMRateLimitError(LLMError):
    """Cota ou limite de requisicoes excedido."""

    code = "llm_rate_limit"
    http_status = 429
    default_message = "Limite de requisicoes do provedor de LLM atingido."
    retryable = True


class LLMAuthenticationError(LLMError):
    """Credencial ausente, invalida ou sem permissao."""

    code = "llm_authentication_error"
    http_status = 500  # e um problema nosso de configuracao, nao do cliente da API
    default_message = "Credencial do provedor de LLM invalida."
    retryable = False


class LLMResponseFormatError(LLMError):
    """O modelo respondeu, mas fora do formato exigido."""

    code = "llm_response_format_error"
    http_status = 502
    default_message = "O modelo respondeu em formato inesperado."
    retryable = True


class LLMConfigurationError(LLMError):
    """Combinacao de configuracao invalida (ex.: provedor sem chave)."""

    code = "llm_configuration_error"
    http_status = 500
    default_message = "Configuracao do provedor de LLM invalida."
    retryable = False
