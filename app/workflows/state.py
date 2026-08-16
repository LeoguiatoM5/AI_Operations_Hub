"""Estado compartilhado do grafo de agentes.

Cada no recebe o estado e devolve um dicionario PARCIAL, com apenas o que mudou. O
LangGraph aplica essas mudancas segundo o "reducer" de cada campo.

Por padrao o reducer e substituicao. Para `errors` e `completed` usamos `operator.add`,
que concatena listas: nos diferentes registram suas ocorrencias sem sobrescrever as dos
outros. Isso importa mais do que parece -- e o que torna a acumulacao segura mesmo se
dois nos rodarem em paralelo no futuro, sem ninguem precisar lembrar de fazer `append`
no lugar certo.
"""

import operator
from typing import Annotated, Any, NotRequired, TypedDict

#: Agentes que o grafo sabe executar hoje. `automation` chega no V4.
EXECUTABLE_AGENTS = ("research", "analysis")


class AgentError(TypedDict):
    """Falha registrada por um no."""

    agent: str
    code: str
    message: str


class WorkflowState(TypedDict):
    """Estado que atravessa o grafo do inicio ao fim."""

    # --- entrada, definida antes da primeira execucao
    execution_id: str
    request_text: str
    input_data: NotRequired[Any]

    # --- produzido pelo orquestrador
    plan: NotRequired[dict[str, Any]]
    #: Fila de agentes a executar. O roteador consome daqui -- e por isso que o caminho
    #: do grafo e DADO, e nao codigo com condicionais.
    pending_agents: NotRequired[list[str]]
    #: Agentes que o plano pediu mas o grafo ainda nao sabe executar.
    skipped_agents: NotRequired[list[str]]

    # --- produzido pelos agentes
    research: NotRequired[dict[str, Any] | None]
    analysis: NotRequired[dict[str, Any] | None]
    report: NotRequired[dict[str, Any] | None]

    # --- acumulados
    errors: Annotated[list[AgentError], operator.add]
    completed: Annotated[list[str], operator.add]

    #: Falha que impede o grafo de continuar (o plano nao pode ser produzido).
    fatal: NotRequired[bool]


def initial_state(*, execution_id: str, request_text: str, input_data: Any = None) -> WorkflowState:
    """Estado inicial de uma execucao."""
    return WorkflowState(
        execution_id=execution_id,
        request_text=request_text,
        input_data=input_data,
        errors=[],
        completed=[],
    )
