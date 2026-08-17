"""Testes do estado do grafo e dos seus reducers."""

import operator

from app.workflows.state import EXECUTABLE_AGENTS, initial_state, split_plan


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


def test_automation_is_executable() -> None:
    """Desde o V4.2 o grafo sabe executar acoes: `automation` saiu de `agents_skipped`."""
    assert "automation" in EXECUTABLE_AGENTS


def test_plan_is_split_into_executable_and_ignored() -> None:
    pendentes, ignorados = split_plan(["research", "telepatia", "automation"])

    assert pendentes == ["research", "automation"]
    assert ignorados == ["telepatia"]


def test_reporter_never_enters_the_queue_nor_the_ignored_list() -> None:
    """O relatorio e o destino final. Nem executa como agente da fila, nem e "pulado"."""
    pendentes, ignorados = split_plan(["analysis", "reporter"])

    assert pendentes == ["analysis"]
    assert ignorados == []


def test_the_order_of_the_plan_is_preserved() -> None:
    """Quem decide a ordem e o orquestrador: a divisao nao pode reordenar nada."""
    pendentes, _ = split_plan(["automation", "analysis", "research"])

    assert pendentes == ["automation", "analysis", "research"]
