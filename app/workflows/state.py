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

#: Agentes que o grafo sabe executar hoje.
EXECUTABLE_AGENTS = ("research", "analysis", "automation")


def split_plan(suggested: list[str]) -> tuple[list[str], list[str]]:
    """Divide o plano entre o que o grafo executa e o que ele nao sabe executar.

    Funcao pura, fora do no, para que a regra seja testavel sem LLM nem banco. Hoje todo
    agente que a triagem pode sugerir e executavel -- mas a segunda lista continua
    existindo, e e o que impede que um agente futuro, adicionado ao schema da triagem e
    esquecido aqui, desapareca do resultado sem deixar rastro.

    O `reporter` nunca entra na fila: ele e o destino final do grafo, e enfileira-lo
    criaria um laco.
    """
    pendentes = [nome for nome in suggested if nome in EXECUTABLE_AGENTS]
    ignorados = [nome for nome in suggested if nome not in EXECUTABLE_AGENTS and nome != "reporter"]
    return pendentes, ignorados


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

    #: Acao escolhida pelo agente de automacao, ainda NAO executada. Vive no estado (e
    #: nao numa variavel local do no) porque e ela que atravessa a pausa: o checkpoint
    #: gravado aqui e o que permite a aprovacao acontecer horas depois, em outro processo.
    tool_call: NotRequired[dict[str, Any] | None]
    #: Desfecho da acao: executada, recusada por um humano, ou nao tentada.
    automation: NotRequired[dict[str, Any] | None]

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
