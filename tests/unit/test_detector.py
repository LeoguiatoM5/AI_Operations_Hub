"""Testes do conjunto que mede o detector.

O conjunto real fica coberto por um teste de contrato: ele carrega, todo caso explica por
que existe, e ha ao menos um controle. Sem controle, um motor que reprovasse tudo passaria
com nota cheia -- e e exatamente o defeito que ninguem procura.
"""

import json
from pathlib import Path

import pytest

from app.core.exceptions import ValidationError
from app.evals.detector import (
    DetectorCase,
    DetectorResult,
    load_detector_cases,
    render_detector_markdown,
    run_detector,
)
from app.quality.base import DimensionScore, QualitySubject
from app.quality.engine import QualityEngine

CASOS = Path(__file__).parents[2] / "evals" / "detector_cases.json"


class DimensaoFixa:
    """Dimensao com nota definida pelo teste."""

    def __init__(self, name: str, score: float) -> None:
        self._name, self._score = name, score

    @property
    def name(self) -> str:
        return self._name

    @property
    def uses_llm(self) -> bool:
        return False

    async def evaluate(self, subject: QualitySubject) -> DimensionScore:
        return DimensionScore(dimension=self._name, score=self._score, reason="fixa")


# ---------------------------------------------------------------- o conjunto real


def test_the_real_set_loads() -> None:
    casos = load_detector_cases(CASOS)

    assert len(casos) >= 6, "poucos casos para dizer onde o limite fica"


def test_there_is_a_control_case() -> None:
    """Um juiz que reprova tudo e tao inutil quanto um que aprova tudo."""
    casos = load_detector_cases(CASOS)

    assert any(caso.defect is None for caso in casos)


def test_every_dimension_that_can_fail_is_exercised() -> None:
    """Uma dimensao sem caso de defeito nunca foi verificada -- e ninguem sabe se ela
    reprova alguma coisa."""
    defeitos = {caso.defect for caso in load_detector_cases(CASOS) if caso.defect}

    assert {"grounding", "relevance", "completeness", "consistency"} <= defeitos


def test_every_case_explains_itself() -> None:
    for caso in load_detector_cases(CASOS):
        assert len(caso.note) > 40, f"{caso.id}: nota curta demais"


def test_a_set_without_control_is_refused(temp_dir: Path) -> None:
    arquivo = temp_dir / "so_defeitos.json"
    arquivo.write_text(
        json.dumps(
            [
                {
                    "id": "x",
                    "note": "nota longa o suficiente para explicar o caso de teste",
                    "task": "t",
                    "answer": "a",
                    "defect": "grounding",
                    "expect_below": 0.5,
                }
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match="controle"):
        load_detector_cases(arquivo)


def test_a_defect_without_a_bound_is_refused() -> None:
    """Declarar o defeito sem dizer o quanto e demais nao verifica nada."""
    with pytest.raises(ValueError, match="andam juntos"):
        DetectorCase.model_validate(
            {"id": "x", "note": "n" * 50, "task": "t", "answer": "a", "defect": "grounding"}
        )


# ---------------------------------------------------------------- a execucao


def caso(**overrides: object) -> DetectorCase:
    base: dict[str, object] = {
        "id": "c",
        "note": "n" * 50,
        "task": "pergunta",
        "answer": "resposta",
    }
    return DetectorCase.model_validate({**base, **overrides})


async def test_a_detected_defect_is_reported_as_detected() -> None:
    motor = QualityEngine([DimensaoFixa("grounding", 0.0)])

    resultado = (await run_detector(motor, [caso(defect="grounding", expect_below=0.5)]))[0]

    assert resultado.detected is True
    assert resultado.dimension_score == 0.0


async def test_a_missed_defect_is_reported_as_missed() -> None:
    """O caso que justifica o conjunto: a dimensao rodou e nao viu o problema."""
    motor = QualityEngine([DimensaoFixa("consistency", 1.0)])

    resultado = (await run_detector(motor, [caso(defect="consistency", expect_below=0.8)]))[0]

    assert resultado.detected is False
    assert resultado.dimension_score == 1.0


async def test_the_control_passes_when_nothing_fails() -> None:
    motor = QualityEngine([DimensaoFixa("relevance", 1.0)], threshold=0.85)

    resultado = (await run_detector(motor, [caso()]))[0]

    assert resultado.detected is True


async def test_the_control_fails_if_the_engine_rejects_a_good_answer() -> None:
    """Detector que reprova o controle esta calibrado para reprovar tudo."""
    motor = QualityEngine([DimensaoFixa("relevance", 0.2)], threshold=0.85)

    resultado = (await run_detector(motor, [caso()]))[0]

    assert resultado.detected is False


# ---------------------------------------------------------------- o relatorio


def resultado(nome: str, defeito: str | None, agregado: float) -> DetectorResult:
    return DetectorResult(
        case_id=nome,
        note="n",
        defect=defeito,
        expect_below=0.5 if defeito else None,
        score=agregado,
        detected=True,
    )


def test_the_report_says_where_the_threshold_can_sit() -> None:
    """E o numero que o conjunto existe para produzir."""
    texto = render_detector_markdown(
        [resultado("bom", None, 0.95), resultado("ruim", "grounding", 0.40)]
    )

    assert "0.40" in texto and "0.95" in texto
    assert "nao se sobrepoem" in texto


def test_the_report_warns_when_the_ranges_overlap() -> None:
    """Sobreposicao significa que o agregado sozinho nao serve de portao -- e e por isso
    que existem dimensoes criticas."""
    texto = render_detector_markdown(
        [resultado("bom", None, 0.60), resultado("ruim", "grounding", 0.80)]
    )

    assert "se sobrepoem" in texto
