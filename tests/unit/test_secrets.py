"""Testes de protecao de segredos.

Vazamento de chave costuma acontecer por acidente: um log de debug que despeja o
objeto de configuracao, um traceback em ferramenta de monitoramento, uma mensagem de
erro copiada para um ticket. SecretStr fecha essa porta -- e este teste garante que
ela continue fechada.
"""

from pydantic import SecretStr

from app.core.config import Settings

CHAVE = "sk-chave-secreta-que-nao-pode-vazar"


def make_settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        llm_provider="openai",
        openai_api_key=SecretStr(CHAVE),
    )


def test_api_key_is_not_exposed_in_repr() -> None:
    assert CHAVE not in repr(make_settings())


def test_api_key_is_not_exposed_in_str() -> None:
    assert CHAVE not in str(make_settings())


def test_api_key_is_not_exposed_when_dumping_the_model() -> None:
    """model_dump() aparece em logs estruturados e em respostas de debug."""
    dumped = str(make_settings().model_dump())

    assert CHAVE not in dumped


def test_api_key_is_available_when_explicitly_requested() -> None:
    """O acesso continua possivel, mas exige uma chamada deliberada."""
    settings = make_settings()

    assert settings.openai_api_key is not None
    assert settings.openai_api_key.get_secret_value() == CHAVE
