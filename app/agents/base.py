"""Contrato comum dos agentes.

Todo agente recebe um pedido, conversa com um LLM e devolve um payload validado junto
com os dados de custo da chamada. Esse formato uniforme e o que permite ao servico
registrar qualquer agente na mesma tabela, sem conhecer o que cada um faz.
"""

from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from app.llm.base import LLMResponse

PROMPTS_DIR = Path(__file__).parent / "prompts"


@dataclass(frozen=True)
class AgentOutcome[T: BaseModel]:
    """Resultado de um passo de agente."""

    agent: str
    action: str
    payload: T
    response: LLMResponse
    repairs: int = 0


def load_prompt(name: str) -> str:
    """Le um prompt do diretorio de prompts.

    Prompts vivem em arquivo, e nao em f-strings espalhadas pelo codigo, porque sao
    artefato versionavel: aparecem no diff, podem ser revisados em pull request e
    trocados sem recompilar raciocinio de negocio.
    """
    path = PROMPTS_DIR / f"{name}.md"
    return path.read_text(encoding="utf-8")
