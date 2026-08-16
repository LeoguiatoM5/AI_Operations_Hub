"""Agente de triagem.

Primeiro passo de toda execucao: interpreta a solicitacao em linguagem natural e a
converte em uma classificacao estruturada, que nas proximas versoes alimentara o
roteamento do grafo de agentes.
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.agents.base import AgentOutcome, load_prompt
from app.core.logging import get_logger
from app.llm.base import LLMMessage, LLMProvider
from app.llm.structured import complete_structured, dump_schema

logger = get_logger(__name__)

Intent = Literal["analise", "relatorio", "automacao", "consulta", "outro"]
Urgency = Literal["baixa", "media", "alta", "critica"]
AgentName = Literal["research", "analysis", "automation", "reporter"]


class TriageResult(BaseModel):
    """Classificacao estruturada de uma solicitacao."""

    intent: Intent = Field(description="Natureza principal da solicitacao.")
    summary: str = Field(
        max_length=400, description="Reformulacao objetiva do que foi pedido, em uma frase."
    )
    entities: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="Sistemas, times, periodos ou documentos citados na solicitacao.",
    )
    urgency: Urgency = Field(description="Urgencia percebida a partir do texto.")
    requires_approval: bool = Field(
        description="True se a solicitacao implicar acao de escrita em sistema externo."
    )
    suggested_agents: list[AgentName] = Field(
        default_factory=list,
        max_length=4,
        description="Agentes necessarios, na ordem de execucao.",
    )
    confidence: float = Field(ge=0.0, le=1.0, description="Certeza sobre a classificacao.")

    @model_validator(mode="after")
    def approval_implies_an_automation_step(self) -> "TriageResult":
        """Rejeita classificacao internamente contraditoria.

        Observado em execucao real: o modelo respondeu `requires_approval: true` com
        `suggested_agents: []`. Se a solicitacao exige aprovacao, ela envolve escrita em
        sistema externo -- e escrita, nesta arquitetura, so passa pelo agente
        `automation`. Um plano sem esse agente nunca executaria o que foi pedido.

        Validar coerencia, e nao apenas tipos, e o que faz o retry dirigido corrigir
        semantica alem de sintaxe. E a mesma ideia que o AI Quality Gateway (V5) aplica
        em escala maior, na dimensao `consistency`.
        """
        if self.requires_approval and "automation" not in self.suggested_agents:
            raise ValueError(
                "requires_approval=true exige que 'automation' conste em suggested_agents, "
                "pois nenhum outro agente executa acoes de escrita"
            )
        return self


class TriageAgent:
    """Classifica a solicitacao do usuario."""

    name = "triage"
    action = "classify_request"

    def __init__(self, provider: LLMProvider, *, repair_attempts: int = 1) -> None:
        self._provider = provider
        self._repair_attempts = repair_attempts

    async def run(self, request_text: str) -> AgentOutcome[TriageResult]:
        system_prompt = load_prompt("triage").replace("{schema}", dump_schema(TriageResult))

        completion = await complete_structured(
            self._provider,
            [LLMMessage.system(system_prompt), LLMMessage.user(request_text)],
            TriageResult,
            repair_attempts=self._repair_attempts,
        )

        logger.info(
            "triage_completed",
            intent=completion.value.intent,
            urgency=completion.value.urgency,
            requires_approval=completion.value.requires_approval,
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
