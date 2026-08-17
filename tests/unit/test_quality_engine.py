"""Testes do motor de qualidade.

O que se protege aqui e a honestidade do numero. Um portao de qualidade que reprova por
engano faz o time desliga-lo em uma semana; um que aprova por engano nao serve para nada.
Quase todo caso abaixo existe por causa de um desses dois lados.
"""

import pytest

from app.core.exceptions import AIHubError
from app.llm.exceptions import LLMTimeoutError
from app.quality.base import DimensionScore, QualityReport, QualitySubject
from app.quality.engine import QualityEngine


class DimensaoFixa:
    """Dimensao com resultado definido pelo teste."""

    def __init__(
        self,
        name: str,
        score: float = 1.0,
        *,
        applicable: bool = True,
        uses_llm: bool = False,
        raises: AIHubError | None = None,
        cost_usd: float = 0.0,
    ) -> None:
        self._name = name
        self._score = score
        self._applicable = applicable
        self._uses_llm = uses_llm
        self._raises = raises
        self._cost = cost_usd

    @property
    def name(self) -> str:
        return self._name

    @property
    def uses_llm(self) -> bool:
        return self._uses_llm

    async def evaluate(self, subject: QualitySubject) -> DimensionScore:
        if self._raises is not None:
            raise self._raises
        return DimensionScore(
            dimension=self._name,
            score=self._score if self._applicable else 0.0,
            applicable=self._applicable,
            reason=f"nota fixa de {self._name}",
            cost_usd=self._cost,
        )


def subject() -> QualitySubject:
    return QualitySubject(task="Analise os chamados criticos.", answer="Dois sao de login.")


# ---------------------------------------------------------------- agregacao


async def test_aggregates_the_dimensions() -> None:
    motor = QualityEngine(
        [DimensaoFixa("relevance", 1.0), DimensaoFixa("completeness", 0.0)],
        weights={"relevance": 1.0, "completeness": 1.0},
    )

    report = await motor.evaluate(subject())

    assert report.score == 0.5


async def test_weights_change_the_result() -> None:
    """Peso nao e enfeite: afirmar sem fonte deve doer mais que escrever curto demais."""
    motor = QualityEngine(
        [DimensaoFixa("grounding", 0.0), DimensaoFixa("completeness", 1.0)],
        weights={"grounding": 3.0, "completeness": 1.0},
    )

    report = await motor.evaluate(subject())

    assert report.score == 0.25


async def test_an_undeclared_dimension_still_counts() -> None:
    """Sem peso definido, cair para zero seria um jeito silencioso de desligar a medicao."""
    motor = QualityEngine([DimensaoFixa("inventada", 0.0)], weights={})

    report = await motor.evaluate(subject())

    assert report.score == 0.0


# ---------------------------------------------------------------- inaplicavel


async def test_an_inapplicable_dimension_leaves_the_average() -> None:
    """Pontuar como zero puniria o sistema por um caso em que ele nao errou."""
    motor = QualityEngine(
        [DimensaoFixa("relevance", 1.0), DimensaoFixa("grounding", applicable=False)]
    )

    report = await motor.evaluate(subject())

    assert report.score == 1.0
    assert len(report.applicable) == 1
    assert len(report.dimensions) == 2, "a dimensao pulada continua visivel no relatorio"


async def test_nothing_measurable_does_not_fail_the_execution() -> None:
    """Nao medir nao e o mesmo que medir mal: o portao nao reprova o que ninguem examinou."""
    motor = QualityEngine([DimensaoFixa("grounding", applicable=False)])

    report = await motor.evaluate(subject())

    assert report.score == 1.0
    assert report.passed is True
    assert report.applicable == []


# ---------------------------------------------------------------- aprovacao


async def test_the_threshold_decides() -> None:
    motor = QualityEngine([DimensaoFixa("relevance", 0.65)], threshold=0.7)

    assert (await motor.evaluate(subject())).passed is False


async def test_exactly_at_the_threshold_passes() -> None:
    motor = QualityEngine([DimensaoFixa("relevance", 0.7)], threshold=0.7)

    assert (await motor.evaluate(subject())).passed is True


async def test_a_critical_dimension_fails_alone() -> None:
    """Media alta pode esconder o pior caso: respondeu bem sobre o assunto certo E
    inventou a fonte."""
    motor = QualityEngine(
        [
            DimensaoFixa("grounding", 0.2),
            DimensaoFixa("relevance", 1.0),
            DimensaoFixa("completeness", 1.0),
            DimensaoFixa("consistency", 1.0),
        ],
        threshold=0.7,
    )

    report = await motor.evaluate(subject())

    assert report.score >= 0.7, "a media sozinha aprovaria"
    assert report.passed is False, "grounding reprova sozinha"


async def test_a_noncritical_dimension_does_not_fail_alone() -> None:
    motor = QualityEngine(
        [DimensaoFixa("completeness", 0.1), DimensaoFixa("relevance", 1.0)],
        weights={"completeness": 0.1, "relevance": 3.0},
        threshold=0.7,
    )

    assert (await motor.evaluate(subject())).passed is True


# ---------------------------------------------------------------- robustez


async def test_a_broken_dimension_does_not_fail_the_execution() -> None:
    """Portao que derruba a resposta porque o proprio portao quebrou inverte a razao de
    existir."""
    motor = QualityEngine(
        [DimensaoFixa("relevance", 1.0), DimensaoFixa("grounding", raises=LLMTimeoutError())]
    )

    report = await motor.evaluate(subject())

    assert report.passed is True
    quebrada = next(d for d in report.dimensions if d.dimension == "grounding")
    assert quebrada.applicable is False
    assert quebrada.evidence["error_code"] == "llm_timeout"


async def test_the_reason_of_the_failure_survives() -> None:
    """Sem o motivo, o retry dirigido nao teria o que dizer ao modelo."""
    motor = QualityEngine(
        [DimensaoFixa("grounding", 0.1), DimensaoFixa("relevance", 0.2)], threshold=0.7
    )

    report = await motor.evaluate(subject())

    assert "grounding" in report.feedback()
    assert "relevance" in report.feedback()


async def test_failures_come_worst_first() -> None:
    """Quem le -- pessoa ou modelo -- deve encontrar o pior problema na primeira linha."""
    motor = QualityEngine(
        [DimensaoFixa("relevance", 0.5), DimensaoFixa("grounding", 0.1)], threshold=0.7
    )

    report = await motor.evaluate(subject())

    assert [item.dimension for item in report.failures] == ["grounding", "relevance"]


async def test_an_approved_report_has_no_feedback() -> None:
    motor = QualityEngine([DimensaoFixa("relevance", 1.0)], threshold=0.7)

    assert (await motor.evaluate(subject())).feedback() == ""


# ---------------------------------------------------------------- custo


async def test_the_cost_of_measuring_is_reported() -> None:
    """Um portao que dobra a conta precisa mostrar isso em vez de esconder."""
    motor = QualityEngine(
        [
            DimensaoFixa("grounding", 1.0, uses_llm=True, cost_usd=0.0002),
            DimensaoFixa("api_reliability", 1.0, cost_usd=0.0),
        ]
    )

    report = await motor.evaluate(subject())

    assert report.cost_usd == 0.0002


def test_an_engine_without_llm_declares_itself_free() -> None:
    """O modo offline roda sobre dezenas de entradas: quem chama precisa poder montar um
    motor barato."""
    assert QualityEngine([DimensaoFixa("api_reliability")]).uses_llm is False
    assert QualityEngine([DimensaoFixa("grounding", uses_llm=True)]).uses_llm is True


# ---------------------------------------------------------------- contrato dos tipos


def test_an_inapplicable_dimension_cannot_carry_a_score() -> None:
    """Inaplicavel com nota alta enganaria tanto quanto com nota baixa."""
    with pytest.raises(ValueError, match="inaplicavel"):
        DimensionScore(dimension="grounding", score=0.9, applicable=False)


def test_the_report_separates_measured_from_skipped() -> None:
    report = QualityReport(
        score=1.0,
        passed=True,
        threshold=0.7,
        dimensions=[
            DimensionScore(dimension="relevance", score=1.0),
            DimensionScore(dimension="grounding", score=0.0, applicable=False),
        ],
    )

    assert [item.dimension for item in report.applicable] == ["relevance"]
