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
