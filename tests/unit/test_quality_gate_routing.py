"""Testes do roteamento apos o portao de qualidade.

`route_after_quality` e funcao pura, como `route_next`: da para percorrer os quatro
desfechos do portao sem provider, sem banco e em milissegundos. E o argumento para o
limite de tentativas viver no roteador, e nao numa configuracao do LangGraph -- assim ele
e inspecionavel por teste, sem executar agente nenhum.
"""

from typing import Any

from langgraph.graph import END

from app.workflows.graph import MAX_REPORT_ATTEMPTS, REPORTER, route_after_quality
from app.workflows.state import WorkflowState, initial_state


def estado(**overrides: Any) -> WorkflowState:
    base = initial_state(execution_id="exec-1", request_text="analise os chamados")
    base.update(overrides)  # type: ignore[typeddict-item]
    return base


def avaliacao(passed: bool, score: float = 0.5) -> dict[str, Any]:
    return {"passed": passed, "score": score, "threshold": 0.7, "dimensions": []}


def test_without_evaluation_the_graph_ends() -> None:
    """Portao desligado: nao ha o que decidir."""
    assert route_after_quality(estado()) == END


def test_an_empty_evaluation_also_ends() -> None:
    assert route_after_quality(estado(quality={})) == END


def test_an_approved_report_ends() -> None:
    assert route_after_quality(estado(quality=avaliacao(True, 0.9), quality_attempts=1)) == END


def test_a_rejected_report_goes_back_for_correction() -> None:
    """Corrigir a redacao e barato perto de reexecutar pesquisa e analise."""
    reprovado = estado(quality=avaliacao(False, 0.4), quality_attempts=1)

    assert route_after_quality(reprovado) == REPORTER


def test_exhausted_attempts_end_even_when_rejected() -> None:
    """Reter a resposta seria pior: quem pediu fica sem nada e o material apurado --
    que custou tokens -- se perde."""
    esgotado = estado(quality=avaliacao(False, 0.4), quality_attempts=MAX_REPORT_ATTEMPTS)

    assert route_after_quality(esgotado) == END


def test_the_loop_is_bounded() -> None:
    """A garantia que impede reporter -> quality -> reporter de girar para sempre."""
    for tentativa in range(1, MAX_REPORT_ATTEMPTS + 5):
        destino = route_after_quality(estado(quality=avaliacao(False), quality_attempts=tentativa))
        if tentativa >= MAX_REPORT_ATTEMPTS:
            assert destino == END, f"tentativa {tentativa} deveria encerrar"
        else:
            assert destino == REPORTER


def test_routing_is_deterministic() -> None:
    atual = estado(quality=avaliacao(False), quality_attempts=1)

    assert {route_after_quality(atual) for _ in range(10)} == {REPORTER}
