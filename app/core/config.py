"""Configuracao da aplicacao.

Toda configuracao entra por variavel de ambiente e e validada na inicializacao.
Se um valor for invalido, o processo falha ao subir -- nao no meio de um request.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "test", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]
LogFormat = Literal["console", "json"]
LLMProviderName = Literal["fake", "openai"]


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

    # LLM
    # O padrao e "fake" de proposito: quem clona o repositorio consegue subir e usar a
    # aplicacao sem possuir nenhuma chave de API, e o CI roda sem segredos.
    llm_provider: LLMProviderName = "fake"
    llm_model: str = "gpt-4o-mini"
    llm_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    llm_max_output_tokens: int = Field(default=1024, gt=0, le=32_000)
    llm_timeout_seconds: float = Field(default=30.0, gt=0)
    llm_max_attempts: int = Field(default=3, ge=1, le=10)
    llm_retry_base_delay_seconds: float = Field(default=0.5, ge=0)

    # SecretStr impede que a chave apareca em repr(), str() ou em um log de debug
    # que despeje o objeto de configuracao inteiro.
    openai_api_key: SecretStr | None = None

    # banco de dados
    # O caminho relativo mantem o arquivo dentro do projeto (e fora do Git, via
    # .gitignore). Trocar para PostgreSQL e mudar apenas esta URL.
    database_url: str = "sqlite+aiosqlite:///./data/app.db"
    database_echo: bool = False

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
