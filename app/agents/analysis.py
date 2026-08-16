"""Agente de analise: encontra padroes nos dados e os sustenta com evidencia."""

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.agents.base import AgentOutcome, load_prompt
from app.core.logging import get_logger
from app.llm.base import LLMMessage, LLMProvider
from app.llm.structured import complete_structured, dump_schema

logger = get_logger(__name__)

Severity = Literal["baixa", "media", "alta", "critica"]


class Finding(BaseModel):
    """Um padrao identificado nos dados."""

    pattern: str = Field(max_length=300, description="O padrao observado, em uma frase.")
    occurrences: int = Field(ge=1, description="Quantas vezes o padrao aparece nos dados.")
    severity: Severity
    evidence: list[str] = Field(
        min_length=1,
        max_length=10,
        description="Trechos ou valores extraidos literalmente dos dados.",
    )


class Indicator(BaseModel):
    """Um numero derivado dos dados."""

    name: str = Field(max_length=100)
    value: float
    unit: str = Field(default="", max_length=30)


class AnalysisResult(BaseModel):
    """Resultado de uma analise."""

    summary: str = Field(min_length=1, max_length=1200)
    findings: list[Finding] = Field(default_factory=list, max_length=20)
    indicators: list[Indicator] = Field(default_factory=list, max_length=20)
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def high_confidence_requires_findings(self) -> "AnalysisResult":
        """Confianca alta sem nenhum achado e contradicao.

        Se o modelo diz estar 90% seguro mas nao encontrou padrao algum, ou ele nao
        analisou os dados, ou a confianca nao significa nada. Nos dois casos o numero
        nao deve ser propagado como se fosse informacao.
        """
        if self.confidence >= 0.7 and not self.findings:
            raise ValueError(
                "confianca alta exige ao menos um achado: sem padrao identificado, a "
                "confianca deve refletir a ausencia de conclusao"
            )
        return self


class AnalysisAgent:
    """Analisa dados estruturados ou texto e devolve achados com evidencia."""

    name = "analysis"
    action = "find_patterns"

    def __init__(self, provider: LLMProvider, *, repair_attempts: int = 1) -> None:
        self._provider = provider
        self._repair_attempts = repair_attempts

    async def run(self, task: str, data: Any) -> AgentOutcome[AnalysisResult]:
        rendered = (
            json.dumps(data, ensure_ascii=False, indent=2) if not isinstance(data, str) else data
        )
        system_prompt = (
            load_prompt("analysis")
            .replace("{data}", rendered)
            .replace("{schema}", dump_schema(AnalysisResult))
        )

        completion = await complete_structured(
            self._provider,
            [LLMMessage.system(system_prompt), LLMMessage.user(task)],
            AnalysisResult,
            repair_attempts=self._repair_attempts,
        )

        logger.info(
            "analysis_completed",
            findings=len(completion.value.findings),
            indicators=len(completion.value.indicators),
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
