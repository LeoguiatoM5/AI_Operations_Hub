"""Testes da camada de configuracao.

A configuracao e a fronteira do sistema: valores invalidos devem quebrar na
inicializacao, nao no meio de um request.
"""

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.core.config import Settings


def test_defaults_are_safe_for_local_development() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.app_env == "local"
    assert settings.log_format == "console"
    assert settings.docs_enabled is True


def test_invalid_log_level_is_rejected_on_startup() -> None:
    with pytest.raises(PydanticValidationError):
        Settings(_env_file=None, log_level="INFOO")  # type: ignore[call-arg,arg-type]


def test_docs_are_disabled_in_production() -> None:
    settings = Settings(_env_file=None, app_env="production")  # type: ignore[call-arg]

    assert settings.is_production is True
    assert settings.docs_enabled is False
