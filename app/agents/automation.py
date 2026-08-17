"""Agente de automacao: escolhe uma ferramenta e monta os argumentos dela.

E o unico agente cuja saida nao e apenas texto estruturado -- ela vira uma acao. Isso
muda o rigor exigido da validacao: um `summary` mal escrito e um relatorio ruim; um
`arguments` mal formado e uma mensagem enviada para o canal errado.

A checagem contra o catalogo (a ferramenta existe? os argumentos batem com o schema
dela?) e passada ao `complete_structured` como validador, e nao feita depois. Assim uma
escolha incoerente vira uma tentativa de reparo dirigida -- o modelo recebe de volta o
motivo exato da rejeicao -- em vez de virar uma falha do no.
"""

import json
from typing import Any

from pydantic import BaseModel, Field

from app.agents.base import AgentOutcome, load_prompt
from app.core.logging import get_logger
from app.llm.base import LLMMessage, LLMProvider
from app.llm.structured import complete_structured, dump_schema
from app.tools.exceptions import ToolInputError
from app.tools.registry import ToolRegistry

logger = get_logger(__name__)


class ToolCall(BaseModel):
    """A acao que o agente pretende executar."""

    tool: str = Field(max_length=64, description="Nome exato de uma ferramenta do catalogo.")
    arguments: dict[str, Any] = Field(
        default_factory=dict, description="Argumentos conforme o input_schema da ferramenta."
    )
    reason: str = Field(
        min_length=1,
        max_length=300,
        description="Por que esta acao atende ao pedido. Lido por quem vai aprovar.",
    )


class AutomationAgent:
    """Traduz uma solicitacao em uma chamada de ferramenta valida."""

    name = "automation"
    action = "choose_tool"

    def __init__(
        self, provider: LLMProvider, registry: ToolRegistry, *, repair_attempts: int = 1
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._repair_attempts = repair_attempts

    def _check_against_catalog(self, call: ToolCall) -> None:
        """Regra que o JSON Schema nao consegue expressar.

        Quais ferramentas existem e o que cada uma aceita e dado de runtime: depende do
        registro montado para esta execucao. Levanta `ValueError` porque e essa a
        linguagem do reparo dirigido em `complete_structured`.
        """
        if call.tool not in self._registry:
            disponiveis = ", ".join(spec.name for spec in self._registry.specs())
            raise ValueError(
                f"A ferramenta '{call.tool}' nao existe. Escolha uma destas: {disponiveis}."
            )

        try:
            self._registry.validate_input(call.tool, call.arguments)
        except ToolInputError as error:
            problemas = "; ".join(
                f"{'.'.join(str(p) for p in item.get('loc', ())) or 'objeto'}: {item.get('msg')}"
                for item in error.details.get("errors", [])
            )
            raise ValueError(
                f"Os argumentos nao satisfazem o schema de '{call.tool}': {problemas}"
            ) from error

    async def run(self, task: str, material: Any = None) -> AgentOutcome[ToolCall]:
        catalogo = json.dumps(
            [spec.model_dump(mode="json") for spec in self._registry.specs()],
            ensure_ascii=False,
            indent=2,
        )
        rendered = (
            material
            if isinstance(material, str)
            else json.dumps(material, ensure_ascii=False, indent=2, default=str)
        )

        system_prompt = (
            load_prompt("automation")
            .replace("{catalog}", catalogo)
            .replace("{material}", rendered)
            .replace("{schema}", dump_schema(ToolCall))
        )

        completion = await complete_structured(
            self._provider,
            [LLMMessage.system(system_prompt), LLMMessage.user(task)],
            ToolCall,
            repair_attempts=self._repair_attempts,
            validate=self._check_against_catalog,
        )

        logger.info(
            "automation_planned",
            tool=completion.value.tool,
            requires_approval=self._registry.requires_approval(completion.value.tool),
            repairs=completion.repairs,
        )

        return AgentOutcome(
            agent=self.name,
            action=self.action,
            payload=completion.value,
            response=completion.response,
            repairs=completion.repairs,
        )
