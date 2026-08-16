"""Agente de relatorio: consolida o trabalho dos demais agentes."""

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.agents.base import AgentOutcome, load_prompt
from app.core.logging import get_logger
from app.llm.base import LLMMessage, LLMProvider
from app.llm.structured import complete_structured, dump_schema

logger = get_logger(__name__)

Priority = Literal["baixa", "media", "alta", "urgente"]


class Recommendation(BaseModel):
    """Uma acao recomendada, amarrada ao material analisado."""

    action: str = Field(max_length=300)
    rationale: str = Field(max_length=600, description="Por que, com base no material.")
    priority: Priority


class Report(BaseModel):
    """Relatorio consolidado."""

    executive_summary: str = Field(min_length=1, max_length=1500)
    key_points: list[str] = Field(default_factory=list, max_length=15)
    recommendations: list[Recommendation] = Field(default_factory=list, max_length=15)
    limitations: list[str] = Field(
        default_factory=list,
        max_length=15,
        description="O que faltou, falhou ou nao pode ser verificado.",
    )
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def degraded_report_must_declare_limitations(self) -> "Report":
        """Um relatorio sem conteudo e sem limitacoes declaradas nao explica nada.

        Se nao ha pontos-chave nem recomendacoes, alguma coisa deu errado no caminho --
        e quem le precisa saber o que, em vez de receber um resumo vazio e confiante.
        """
        if not self.key_points and not self.recommendations and not self.limitations:
            raise ValueError(
                "relatorio sem pontos-chave e sem recomendacoes precisa declarar em "
                "limitations o que impediu a analise"
            )
        return self


class ReporterAgent:
    """Consolida os resultados dos agentes em um relatorio executivo."""

    name = "reporter"
    action = "write_report"

    def __init__(self, provider: LLMProvider, *, repair_attempts: int = 1) -> None:
        self._provider = provider
        self._repair_attempts = repair_attempts

    async def run(self, task: str, material: dict[str, Any]) -> AgentOutcome[Report]:
        system_prompt = (
            load_prompt("reporter")
            .replace("{material}", json.dumps(material, ensure_ascii=False, indent=2))
            .replace("{schema}", dump_schema(Report))
        )

        completion = await complete_structured(
            self._provider,
            [LLMMessage.system(system_prompt), LLMMessage.user(task)],
            Report,
            repair_attempts=self._repair_attempts,
        )

        logger.info(
            "report_completed",
            key_points=len(completion.value.key_points),
            recommendations=len(completion.value.recommendations),
            limitations=len(completion.value.limitations),
            confidence=completion.value.confidence,
            repairs=completion.repairs,
        )

        return AgentOutcome(
            agent=self.name,
            action=self.action,
            payload=completion.value,
            response=completion.response,
            repairs=completion.repairs,
        )
