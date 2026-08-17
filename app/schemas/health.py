"""Schemas do endpoint de saude."""

from typing import Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Estado da aplicacao."""

    status: Literal["ok", "degraded"] = Field(description="Estado geral do servico.")
    app: str = Field(description="Nome da aplicacao.")
    version: str = Field(description="Versao em execucao.")
    environment: str = Field(description="Ambiente configurado.")
    uptime_seconds: float = Field(description="Tempo desde a inicializacao do processo.")
    llm_provider: str = Field(description="Provedor de LLM em uso.")
    llm_model: str = Field(description="Modelo configurado.")
    #: A pergunta operacional que isto responde: esta instancia envia mensagem de
    #: verdade, ou guarda em memoria? Descobrir isso lendo o `.env` do servidor e mais
    #: dificil do que deveria -- e a resposta muda o que uma aprovacao significa.
    notifier: str = Field(description="Canal de saida das acoes de escrita.")
