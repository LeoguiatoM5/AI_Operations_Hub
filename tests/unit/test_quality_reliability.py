"""Testes da dimensao de confiabilidade.

A unica das cinco que nao custa nada: le o que ja esta gravado em `agent_executions`.
"""

from app.quality.base import QualitySubject, StepFacts
from app.quality.reliability import ApiReliabilityDimension


def passo(agent: str = "research", *, ok: bool = True, attempts: int = 1, erro: str | None = None):
    return StepFacts(agent=agent, action="acao", succeeded=ok, attempts=attempts, error_code=erro)


def subject(*passos: StepFacts) -> QualitySubject:
    return QualitySubject(task="Analise os chamados.", answer="Resposta.", steps=list(passos))


DIMENSAO = ApiReliabilityDimension()


async def test_a_clean_run_scores_full() -> None:
    nota = await DIMENSAO.evaluate(subject(passo(), passo("analysis"), passo("reporter")))

    assert nota.score == 1.0
    assert "sem erro" in nota.reason


async def test_a_failed_step_lowers_the_score() -> None:
    nota = await DIMENSAO.evaluate(
        subject(passo(), passo("analysis", ok=False, erro="llm_timeout"), passo("reporter"))
    )

    assert nota.score < 1.0
    assert nota.evidence["failed"] == 1
    assert nota.evidence["error_codes"] == ["llm_timeout"]


async def test_everything_failing_scores_zero() -> None:
    nota = await DIMENSAO.evaluate(subject(passo(ok=False), passo("analysis", ok=False)))

    assert nota.score == 0.0


async def test_a_retry_costs_less_than_a_failure() -> None:
    """O passo que repetiu terminou certo -- mas pagou o dobro em tokens, e um sistema
    que so acerta na segunda tentativa e fragil."""
    com_repeticao = await DIMENSAO.evaluate(subject(passo(attempts=2), passo("analysis")))
    com_falha = await DIMENSAO.evaluate(subject(passo(ok=False), passo("analysis")))

    assert com_falha.score < com_repeticao.score < 1.0


async def test_the_score_never_goes_below_zero() -> None:
    """Muitas repeticoes num passo so poderiam empurrar a penalidade alem do limite."""
    nota = await DIMENSAO.evaluate(subject(passo(ok=False, attempts=10)))

    assert nota.score == 0.0


async def test_the_reason_names_who_failed() -> None:
    """A frase vai para o relatorio de evals e para o retry dirigido."""
    nota = await DIMENSAO.evaluate(
        subject(passo("research", ok=False, erro="llm_rate_limit"), passo("reporter"))
    )

    assert "research" in nota.reason
    assert "llm_rate_limit" in nota.reason


async def test_the_reason_mentions_retries() -> None:
    nota = await DIMENSAO.evaluate(subject(passo(attempts=3)))

    assert "tentativa" in nota.reason
    assert nota.evidence["extra_attempts"] == 2


async def test_without_steps_it_declares_itself_inapplicable() -> None:
    """Acontece no modo offline: a entrada do dataset descreve pergunta e resposta
    esperada, nao uma execucao."""
    nota = await DIMENSAO.evaluate(QualitySubject(task="pergunta", answer="resposta"))

    assert nota.applicable is False
    assert nota.score == 0.0


async def test_measuring_costs_nothing() -> None:
    """E o argumento para esta dimensao existir antes das outras quatro."""
    nota = await DIMENSAO.evaluate(subject(passo()))

    assert nota.cost_usd == 0.0
    assert nota.tokens == 0
    assert DIMENSAO.uses_llm is False
