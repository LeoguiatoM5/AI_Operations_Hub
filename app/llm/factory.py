"""Construcao do provider a partir da configuracao.

Este e o unico ponto do projeto que decide qual provedor sera usado. Trocar de
provedor e mudar uma variavel de ambiente -- nenhum modulo de negocio muda.
"""

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.retry import RetryPolicy
from app.llm.base import LLMProvider
from app.llm.exceptions import LLMConfigurationError
from app.llm.fake_provider import FakeLLMProvider
from app.llm.openai_provider import OpenAIProvider
from app.llm.retrying import RetryingLLMProvider

logger = get_logger(__name__)


def _build_raw_provider(settings: Settings) -> LLMProvider:
    """Instancia o provider concreto, sem a camada de retry."""
    match settings.llm_provider:
        case "fake":
            return FakeLLMProvider()
        case "openai":
            if settings.openai_api_key is None:
                raise LLMConfigurationError(
                    "LLM_PROVIDER=openai exige OPENAI_API_KEY definida.",
                    details={"provider": "openai"},
                )
            return OpenAIProvider(
                api_key=settings.openai_api_key.get_secret_value(),
                model=settings.llm_model,
                temperature=settings.llm_temperature,
                max_output_tokens=settings.llm_max_output_tokens,
                timeout_seconds=settings.llm_timeout_seconds,
            )

    # Inalcancavel enquanto Settings.llm_provider for um Literal fechado; a guarda
    # existe para que adicionar um provedor sem implementa-lo falhe alto e cedo.
    raise LLMConfigurationError(
        f"Provedor de LLM nao suportado: {settings.llm_provider!r}.",
        details={"provider": settings.llm_provider},
    )


def build_llm_provider(settings: Settings) -> LLMProvider:
    """Devolve o provider configurado, ja envolvido pela politica de retry."""
    provider = _build_raw_provider(settings)
    policy = RetryPolicy(
        max_attempts=settings.llm_max_attempts,
        base_delay_seconds=settings.llm_retry_base_delay_seconds,
    )
    logger.info(
        "llm_provider_built",
        provider=provider.name,
        model=provider.model,
        max_attempts=policy.max_attempts,
    )
    return RetryingLLMProvider(provider, policy)
