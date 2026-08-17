"""Testes do conjunto de avaliacao e das verificacoes deterministicas.

Incluem um teste de contrato sobre o conjunto REAL: ele valida, os ids nao se repetem e
todo caso explica por que existe. Sem isso, o arquivo apodrece -- casos entram sem motivo
declarado e saem sem que ninguem note.
"""

import json
from pathlib import Path

import pytest

from app.core.exceptions import ValidationError
from app.evals.assertions import check_answered, check_forbidden, check_sources, normalize
from app.evals.dataset import EvalCase, load_dataset
from app.evals.report import CaseResult, EvalReport, render_markdown
from app.quality.base import DimensionScore, QualityReport

DATASET = Path(__file__).parents[2] / "evals" / "evaluation_dataset.json"
CORPUS = Path(__file__).parents[2] / "evals" / "corpus"


def caso(**overrides: object) -> EvalCase:
    base: dict[str, object] = {
        "id": "c1",
        "question": "Qual o prazo de reembolso?",
        "note": "caso de teste",
    }
    return EvalCase.model_validate({**base, **overrides})


# ---------------------------------------------------------------- o conjunto real


def test_the_real_dataset_is_valid() -> None:
    casos = load_dataset(DATASET)

    assert len(casos) >= 15, "conjunto pequeno demais para dizer alguma coisa"


def test_every_case_explains_why_it_exists() -> None:
    """Um caso cujo motivo ninguem lembra e um caso que sera apagado no primeiro dia em
    que der trabalho."""
    for item in load_dataset(DATASET):
        assert len(item.note) > 30, f"{item.id}: nota curta demais para explicar algo"


def test_the_dataset_has_refusal_cases() -> None:
    """Sem eles o conjunto so mede se o sistema responde -- e nao se ele sabe calar."""
    recusas = [item for item in load_dataset(DATASET) if not item.should_answer]

    assert len(recusas) >= 4


def test_expected_sources_exist_in_the_corpus() -> None:
    """Um caso que exige um arquivo inexistente falharia para sempre, por engano de
    digitacao, e pareceria regressao do sistema."""
    arquivos = {caminho.name for caminho in CORPUS.glob("*.md")}

    for item in load_dataset(DATASET):
        for nome in item.expected_sources:
            assert nome in arquivos, f"{item.id}: {nome} nao existe no corpus"


# ---------------------------------------------------------------- carregamento


def test_duplicated_ids_are_refused(temp_dir: Path) -> None:
    """Ids repetidos esconderiam a regressao de um caso atras do outro.

    Usa `temp_dir` (mkdtemp) e nao o `tmp_path` do pytest -- que quebrou nesta maquina
    com `PermissionError` no diretorio de nome fixo `pytest-of-<usuario>`, o cenario que
    a fixture do projeto ja previa desde o V1.
    """
    arquivo = temp_dir / "d.json"
    arquivo.write_text(
        json.dumps(
            [
                {"id": "x", "question": "a?", "note": "nota suficientemente longa aqui"},
                {"id": "x", "question": "b?", "note": "outra nota suficientemente longa"},
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match="repetidos"):
        load_dataset(arquivo)


def test_a_missing_dataset_says_so(temp_dir: Path) -> None:
    with pytest.raises(ValidationError, match="nao encontrado"):
        load_dataset(temp_dir / "inexistente.json")


def test_a_refusal_case_cannot_demand_sources() -> None:
    """Se ha fonte a citar, havia cobertura -- e a recusa estaria errada."""
    with pytest.raises(ValueError, match="expected_sources"):
        caso(should_answer=False, expected_sources=["politica-reembolso.md"])


# ---------------------------------------------------------------- assercoes


def test_answering_when_it_should_passes() -> None:
    assert check_answered(caso(), answered=True).passed


def test_refusing_when_it_should_answer_fails() -> None:
    resultado = check_answered(caso(), answered=False)

    assert not resultado.passed
    assert "recusou" in resultado.detail.lower()


def test_answering_when_it_should_refuse_fails() -> None:
    """A falha mais grave que o conjunto pega: o sistema respondeu o que nao sabia."""
    resultado = check_answered(caso(should_answer=False), answered=True)

    assert not resultado.passed
    assert "recusado" in resultado.detail.lower()


def test_citing_more_than_expected_is_not_an_error() -> None:
    """Exigir o conjunto exato transformaria uma resposta melhor em reprovacao."""
    alvo = caso(expected_sources=["politica-reembolso.md"])

    resultado = check_sources(alvo, cited=["politica-reembolso.md", "politica-ferias.md"])

    assert resultado.passed


def test_a_missing_source_fails_and_says_which() -> None:
    alvo = caso(expected_sources=["politica-reembolso.md"])

    resultado = check_sources(alvo, cited=["politica-ferias.md"])

    assert not resultado.passed
    assert "politica-reembolso.md" in resultado.detail


def test_a_forbidden_claim_is_found_despite_case_and_accent() -> None:
    """Uma alucinacao escrita com maiuscula continua sendo uma alucinacao."""
    alvo = caso(forbidden_claims=["60 dias"])

    assert not check_forbidden(alvo, answer="O prazo e de 60 DIAS.").passed


def test_normalization_removes_accents() -> None:
    assert normalize("Reembolso É Válido") == "reembolso e valido"


def test_without_forbidden_claims_the_check_passes() -> None:
    assert check_forbidden(caso(), answer="qualquer coisa").passed


# ---------------------------------------------------------------- relatorio


def resultado(case_id: str, *, passed: bool, score: float | None = None) -> CaseResult:
    from app.evals.assertions import AssertionResult

    return CaseResult(
        case_id=case_id,
        question="p?",
        note="n",
        assertions=[AssertionResult(name="answered_as_expected", passed=passed, detail="d")],
        quality=(
            None
            if score is None
            else QualityReport(
                score=score,
                passed=score >= 0.7,
                threshold=0.7,
                dimensions=[DimensionScore(dimension="relevance", score=score)],
            )
        ),
        cost_usd=0.001,
    )


def test_the_verdict_rests_on_the_assertions_not_the_scores() -> None:
    """As assercoes nao tem vies, nao custam e nao mudam entre execucoes."""
    report = EvalReport(cases=[resultado("a", passed=True, score=0.1)])

    assert report.pass_rate == 1.0


def test_the_dimension_average_ignores_inapplicable() -> None:
    report = EvalReport(
        cases=[
            resultado("a", passed=True, score=0.8),
            resultado("b", passed=True, score=0.4),
            resultado("c", passed=True),  # sem avaliacao
        ]
    )

    assert report.dimension_averages() == {"relevance": 0.6}


def test_failures_are_counted_by_assertion() -> None:
    report = EvalReport(cases=[resultado("a", passed=False), resultado("b", passed=False)])

    assert report.assertion_failures() == {"answered_as_expected": 2}


def test_a_fake_embedding_run_warns_in_the_report() -> None:
    """Numeros de uma rodada com embeddings lexicais medem encanamento, nao qualidade --
    e quem le trinta dias depois nao tem como saber disso sozinho."""
    texto = render_markdown(
        EvalReport(embedding_provider="fake", cases=[resultado("a", passed=True)])
    )

    assert "embeddings falsos" in texto


def test_the_report_header_records_the_configuration() -> None:
    """Duas rodadas com configuracoes diferentes produzem numeros que nao se comparam."""
    texto = render_markdown(
        EvalReport(
            llm_provider="openai",
            llm_model="gpt-4o-mini",
            embedding_provider="openai",
            embedding_model="text-embedding-3-small",
            min_relevant_score=0.35,
            cases=[resultado("a", passed=True)],
        )
    )

    assert "gpt-4o-mini" in texto
    assert "0.35" in texto


def test_a_failing_case_gets_its_reason_in_the_report() -> None:
    texto = render_markdown(EvalReport(cases=[resultado("quebrado", passed=False)]))

    assert "quebrado" in texto
    assert "Por que este caso existe" in texto
