"""Testes do estado do grafo e dos seus reducers."""

import operator

from app.workflows.state import EXECUTABLE_AGENTS, initial_state


def test_initial_state_starts_with_empty_accumulators() -> None:
    estado = initial_state(execution_id="exec-1", request_text="tarefa")

    assert estado["errors"] == []
    assert estado["completed"] == []
    assert estado["execution_id"] == "exec-1"


def test_input_data_is_optional() -> None:
    assert initial_state(execution_id="e", request_text="t")["input_data"] is None


def test_accumulator_reducer_concatenates_instead_of_replacing() -> None:
    """`operator.add` e o que permite dois nos registrarem sem sobrescrever um ao outro.

    Com o reducer padrao (substituicao), o segundo no apagaria o registro do primeiro --
    e o rastro da execucao ficaria com apenas a ultima etapa.
    """
    do_primeiro = ["orchestrator"]
    do_segundo = ["research"]

    assert operator.add(do_primeiro, do_segundo) == ["orchestrator", "research"]


def test_reporter_is_not_an_executable_agent_in_the_queue() -> None:
    """O relatorio e sempre o destino final, nunca um item da fila -- senao haveria laco."""
    assert "reporter" not in EXECUTABLE_AGENTS


def test_automation_is_not_executable_yet() -> None:
    assert "automation" not in EXECUTABLE_AGENTS
