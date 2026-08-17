"""Testes do roteamento do grafo.

O roteador e funcao pura do estado para o nome do proximo no: sem LLM, sem banco, sem
rede. Por isso da para percorrer todos os caminhos do grafo em milissegundos -- que e
justamente o argumento para o roteamento ser uma `conditional_edge` e nao um `if` dentro
de um no.
"""

from typing import Any

from langgraph.graph import END

from app.workflows.graph import (
    ANALYSIS,
    AUTOMATION,
    AUTOMATION_PLAN,
    REPORTER,
    RESEARCH,
    route_next,
)
from app.workflows.state import WorkflowState, initial_state


def estado(**overrides: Any) -> WorkflowState:
    base = initial_state(execution_id="exec-1", request_text="analise os chamados")
    base.update(overrides)  # type: ignore[typeddict-item]
    return base


def test_fatal_error_ends_the_graph() -> None:
    """Sem plano nao ha caminho: e a unica falha que interrompe o fluxo."""
    assert route_next(estado(fatal=True)) == END


def test_fatal_wins_over_pending_agents() -> None:
    assert route_next(estado(fatal=True, pending_agents=[RESEARCH])) == END


def test_first_pending_agent_is_chosen() -> None:
    assert route_next(estado(pending_agents=[RESEARCH, ANALYSIS])) == RESEARCH


def test_order_of_the_plan_is_respected() -> None:
    """O caminho e DADO: quem decide a ordem e o plano do orquestrador."""
    assert route_next(estado(pending_agents=[ANALYSIS, RESEARCH])) == ANALYSIS


def test_empty_queue_goes_to_the_reporter() -> None:
    assert route_next(estado(pending_agents=[])) == REPORTER


def test_missing_queue_goes_to_the_reporter() -> None:
    assert route_next(estado()) == REPORTER


def test_unknown_agents_are_ignored() -> None:
    """Agente que o grafo nao conhece nao tem no de destino: o fluxo segue sem ele."""
    assert route_next(estado(pending_agents=["telepatia"])) == REPORTER


def test_known_agent_after_unknown_is_still_reached() -> None:
    assert route_next(estado(pending_agents=["telepatia", ANALYSIS])) == ANALYSIS


def test_automation_enters_through_the_planning_node() -> None:
    """A fila fala em AGENTES; o grafo, em NOS. A automacao ocupa dois nos, e o roteador
    so conhece a porta de entrada -- de dentro dela, o caminho e uma aresta fixa."""
    assert route_next(estado(pending_agents=[AUTOMATION])) == AUTOMATION_PLAN


def test_automation_respects_the_order_of_the_plan() -> None:
    assert route_next(estado(pending_agents=[ANALYSIS, AUTOMATION])) == ANALYSIS


def test_errors_do_not_stop_the_flow() -> None:
    """Falha de um agente e degradacao, nao interrupcao."""
    com_erro = estado(
        pending_agents=[ANALYSIS],
        errors=[{"agent": "research", "code": "llm_timeout", "message": "timeout"}],
    )

    assert route_next(com_erro) == ANALYSIS


def test_routing_is_deterministic() -> None:
    atual = estado(pending_agents=[RESEARCH, ANALYSIS])

    assert {route_next(atual) for _ in range(10)} == {RESEARCH}
