"""Testes da selecao de provider e da camada de retry aplicada sobre ele."""

import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.core.retry import RetryPolicy
from app.llm.base import LLMMessage, LLMProvider
from app.llm.exceptions import LLMAuthenticationError, LLMConfigurationError, LLMTimeoutError
from app.llm.factory import build_llm_provider
from app.llm.fake_provider import FakeLLMProvider
from app.llm.retrying import RetryingLLMProvider

MESSAGES = [LLMMessage.user("ping")]

FAST_POLICY = RetryPolicy(max_attempts=3, base_delay_seconds=0.0, jitter=False)


def make_settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


@pytest.fixture
def no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr("asyncio.sleep", fake_sleep)


def test_default_provider_requires_no_api_key() -> None:
    """Requisito de produto: o projeto roda logo apos o clone, sem credencial."""
    provider = build_llm_provider(make_settings())

    assert provider.name == "fake"
    assert isinstance(provider, LLMProvider)


def test_switching_provider_is_a_configuration_change() -> None:
    provider = build_llm_provider(
        make_settings(llm_provider="openai", openai_api_key=SecretStr("sk-teste"))
    )

    assert provider.name == "openai"
    assert provider.model == "gpt-4o-mini"


def test_openai_without_key_fails_with_a_clear_error() -> None:
    with pytest.raises(LLMConfigurationError) as exc_info:
        build_llm_provider(make_settings(llm_provider="openai"))

    assert "OPENAI_API_KEY" in exc_info.value.message


def test_provider_is_wrapped_with_retry() -> None:
    provider = build_llm_provider(make_settings())

    assert isinstance(provider, RetryingLLMProvider)


@pytest.mark.usefixtures("no_real_sleep")
async def test_retry_wrapper_recovers_from_transient_failure() -> None:
    inner = FakeLLMProvider(script=[LLMTimeoutError(), LLMTimeoutError(), "recuperado"])
    provider = RetryingLLMProvider(inner, FAST_POLICY)

    response = await provider.complete(MESSAGES)

    assert response.content == "recuperado"
    assert response.attempts == 3  # a observabilidade registra o esforco real


@pytest.mark.usefixtures("no_real_sleep")
async def test_retry_wrapper_gives_up_on_permanent_failure() -> None:
    inner = FakeLLMProvider(script=[LLMAuthenticationError(), "nunca chega aqui"])
    provider = RetryingLLMProvider(inner, FAST_POLICY)

    with pytest.raises(LLMAuthenticationError):
        await provider.complete(MESSAGES)

    assert inner.call_count == 1
