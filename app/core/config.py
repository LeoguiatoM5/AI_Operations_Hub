"""Configuracao da aplicacao.

Toda configuracao entra por variavel de ambiente e e validada na inicializacao.
Se um valor for invalido, o processo falha ao subir -- nao no meio de um request.
"""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "test", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]
LogFormat = Literal["console", "json"]


class Settings(BaseSettings):
    """Configuracao tipada, carregada de variaveis de ambiente e do arquivo .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # aplicacao
    app_name: str = "AI Operations Hub"
    app_env: Environment = "local"
    api_host: str = "127.0.0.1"
    api_port: int = 8000

    # logs
    log_level: LogLevel = "INFO"
    log_format: LogFormat = "console"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def docs_enabled(self) -> bool:
        """Swagger fica exposto apenas fora de producao."""
        return not self.is_production


@lru_cache
def get_settings() -> Settings:
    """Instancia unica de Settings.

    O cache existe para que ler configuracao nao releia o disco a cada request,
    e para que o mesmo objeto seja compartilhado por toda a aplicacao.
    Em testes, use `get_settings.cache_clear()` apos alterar variaveis de ambiente.
    """
    return Settings()
