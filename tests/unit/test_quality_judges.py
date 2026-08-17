"""Testes das dimensoes julgadas por LLM.

O provider e falso e com roteiro: o juiz devolve exatamente a classificacao que o teste
mandar. Isso permite exercitar o que interessa -- a aritmetica sobre os rotulos, os
atalhos que evitam pagar, e o que acontece quando o juiz responde algo estranho -- sem
rede, sem custo e sem depender do humor de um modelo.

E o argumento central do V5.2: como a nota vem de contagem sobre rotulos, e nao de um
numero pedido ao modelo, ela e **testavel**.
"""

import json
from typing import Any

from app.llm.exceptions import LLMTimeoutError
from app.llm.fake_provider import FakeLLMProvider
from app.quality.base import CitedSource, Expectations, QualitySubject
from app.quality.completeness import CompletenessDimension
from app.quality.consistency import ConsistencyDimension
from app.quality.factory import build_quality_engine
from app.quality.grounding import GroundingDimension
from app.quality.judge import JUDGE_TEMPERATURE
from app.quality.relevance import RelevanceDimension

FONTES = [
    CitedSource(document_id="d1", filename="politica.md", excerpt="O prazo e de 30 dias."),
    CitedSource(document_id="d2", filename="ferias.md", excerpt="Periodo aquisitivo: 12 meses."),
]


def rag_subject(**overrides: Any) -> QualitySubject:
    base: dict[str, Any] = {
        "task": "Qual o prazo para solicitar reembolso?",
        "answer": "O prazo e de 30 dias corridos.",
        "claims": ["O prazo e de 30 dias corridos."],
        "sources": FONTES,
        "answered": True,
        "source_based": True,
    }
    return QualitySubject(**{**base, **overrides})


def verdicts_json(*pares: tuple[str, bool]) -> str:
    return json.dumps(
        {
            "verdicts": [
                {"claim": texto, "supported": ok, "source_index": 1 if ok else None, "note": "x"}
                for texto, ok in pares
            ]
        },
        ensure_ascii=False,
    )


# ---------------------------------------------------------------- grounding


async def test_all_claims_supported_scores_full() -> None:
    provider = FakeLLMProvider(script=[verdicts_json(("O prazo e de 30 dias corridos.", True))])

    nota = await GroundingDimension(provider).evaluate(rag_subject())

    assert nota.score == 1.0
    assert nota.evidence["supported"] == 1


async def test_the_score_is_the_ratio_of_supported_claims() -> None:
    """A nota vem de contagem sobre rotulos -- nao de um numero pedido ao modelo."""
    subject = rag_subject(claims=["a", "b", "c", "d"])
    provider = FakeLLMProvider(
        script=[verdicts_json(("a", True), ("b", True), ("c", True), ("d", False))]
    )

    nota = await GroundingDimension(provider).evaluate(subject)

    assert nota.score == 0.75


async def test_an_unsupported_claim_appears_in_the_evidence() -> None:
    """Sem a afirmacao citada, quem le a nota nao consegue conferir o veredito."""
    subject = rag_subject(claims=["O prazo e de 30 dias.", "O reembolso e automatico."])
    provider = FakeLLMProvider(
        script=[
            verdicts_json(("O prazo e de 30 dias.", True), ("O reembolso e automatico.", False))
        ]
    )

    nota = await GroundingDimension(provider).evaluate(subject)

    assert nota.evidence["unsupported"][0]["claim"] == "O reembolso e automatico."
    assert "O reembolso e automatico." in nota.reason


async def test_a_verdict_about_a_claim_we_never_sent_is_discarded() -> None:
    """Juiz que inventa uma afirmacao e a aprova inflaria a nota. Ja observado em modelos
    menores em tarefas de listagem."""
    subject = rag_subject(claims=["a"])
    provider = FakeLLMProvider(script=[verdicts_json(("a", False), ("inventada", True))])

    nota = await GroundingDimension(provider).evaluate(subject)

    assert nota.score == 0.0, "a afirmacao inventada nao pode salvar a nota"
    assert nota.evidence["verdicts_discarded"] == 1


async def test_matching_counts_are_paired_by_position() -> None:
    """Quando vem um veredito por afirmacao, vale a posicao -- o texto e apenas o eco, e
    o modelo o reescreve.

    Este caso nasceu de um falso negativo achado pelo conjunto de avaliacao: respostas
    quase literais do documento tiravam `grounding = 0` porque o eco vinha diferente. Como
    grounding reprova sozinha, o defeito rejeitaria respostas corretas em producao.
    """
    subject = rag_subject(claims=["O prazo e de 30 dias corridos, conforme a politica."])
    provider = FakeLLMProvider(
        script=[verdicts_json(("o prazo E de 30 dias corridos conforme a politica", True))]
    )

    nota = await GroundingDimension(provider).evaluate(subject)

    assert nota.score == 1.0
    assert nota.evidence["verdicts_discarded"] == 0
    # A evidencia mostra a afirmacao QUE ENVIAMOS, e nao o eco do modelo.
    assert nota.evidence["claims_checked"] == 1


async def test_the_judge_receives_the_sources_numbered() -> None:
    provider = FakeLLMProvider(script=[verdicts_json(("O prazo e de 30 dias corridos.", True))])

    await GroundingDimension(provider).evaluate(rag_subject())

    prompt = provider.calls[0][0].content
    assert "[1]" in prompt and "[2]" in prompt
    assert "O prazo e de 30 dias." in prompt


# ---------------------------------------------------------------- atalhos sem custo


async def test_an_answer_not_based_on_sources_is_inapplicable() -> None:
    """Analise de dados fornecidos pelo usuario nao tem o que fundamentar."""
    provider = FakeLLMProvider(script=["{}"])

    nota = await GroundingDimension(provider).evaluate(
        QualitySubject(task="Analise estes dados.", answer="Dois padroes.", source_based=False)
    )

    assert nota.applicable is False
    assert provider.call_count == 0, "atalho nao pode gastar chamada"


async def test_a_correct_refusal_is_not_punished() -> None:
    """Punir a recusa empurraria o sistema a inventar -- o oposto do que o projeto garante."""
    provider = FakeLLMProvider(script=["{}"])

    nota = await GroundingDimension(provider).evaluate(rag_subject(answered=False, sources=[]))

    assert nota.applicable is False
    assert provider.call_count == 0


async def test_claiming_without_any_source_scores_zero_without_asking() -> None:
    """O defeito e evidente: perguntar a um juiz seria pagar para confirmar o obvio."""
    provider = FakeLLMProvider(script=["{}"])

    nota = await GroundingDimension(provider).evaluate(rag_subject(sources=[]))

    assert nota.score == 0.0
    assert nota.applicable is True
    assert provider.call_count == 0


# ---------------------------------------------------------------- relevance


async def test_an_off_topic_answer_scores_zero() -> None:
    provider = FakeLLMProvider(
        script=[
            json.dumps({"addresses_request": False, "off_topic": [], "reason": "outro assunto"})
        ]
    )

    nota = await RelevanceDimension(provider).evaluate(rag_subject())

    assert nota.score == 0.0
    assert nota.reason == "outro assunto"


async def test_digressions_dilute_but_do_not_invalidate() -> None:
    provider = FakeLLMProvider(
        script=[json.dumps({"addresses_request": True, "off_topic": ["a", "b"], "reason": "ok"})]
    )

    nota = await RelevanceDimension(provider).evaluate(rag_subject())

    assert 0.0 < nota.score < 1.0


async def test_the_judge_runs_at_temperature_zero() -> None:
    """Avaliacao que muda de nota a cada execucao mede o ruido do medidor."""
    assert JUDGE_TEMPERATURE == 0.0


# ---------------------------------------------------------------- consistency


async def test_material_without_contradictions_scores_full() -> None:
    provider = FakeLLMProvider(script=[json.dumps({"contradictions": []})])

    nota = await ConsistencyDimension(provider).evaluate(rag_subject())

    assert nota.score == 1.0


async def test_each_contradiction_costs() -> None:
    provider = FakeLLMProvider(
        script=[
            json.dumps(
                {
                    "contradictions": [
                        {
                            "statement_a": "sao 30 dias",
                            "statement_b": "sao 60 dias",
                            "explanation": "x",
                        }
                    ]
                }
            )
        ]
    )

    nota = await ConsistencyDimension(provider).evaluate(rag_subject())

    assert nota.score < 1.0
    assert nota.evidence["contradictions"][0]["a"] == "sao 30 dias"


async def test_a_finding_without_both_sides_quoted_is_discarded() -> None:
    """Achado sem as duas citacoes e impressao -- e impressao infalsificavel e pior que
    nenhum achado, porque parece trabalho."""
    provider = FakeLLMProvider(
        script=[
            json.dumps(
                {"contradictions": [{"statement_a": "algo", "statement_b": "", "explanation": "x"}]}
            )
        ]
    )

    nota = await ConsistencyDimension(provider).evaluate(rag_subject())

    assert nota.score == 1.0
    assert nota.evidence["discarded"] == 1


# ---------------------------------------------------------------- completeness


async def test_the_score_is_the_ratio_of_covered_items() -> None:
    provider = FakeLLMProvider(
        script=[
            json.dumps(
                {
                    "items": [
                        {"item": "analisar", "covered": True, "note": ""},
                        {"item": "relatorio", "covered": False, "note": "faltou"},
                    ]
                }
            )
        ]
    )

    nota = await CompletenessDimension(provider).evaluate(rag_subject())

    assert nota.score == 0.5
    assert nota.evidence["missing"] == ["relatorio"]


async def test_declared_expectations_go_into_the_prompt() -> None:
    """Offline, quem define o que era exigido e uma pessoa -- nao o modelo que respondeu."""
    subject = rag_subject(
        expectations=Expectations(expected_topics=["prazo de reembolso", "documentos exigidos"])
    )
    provider = FakeLLMProvider(
        script=[
            json.dumps({"items": [{"item": "prazo de reembolso", "covered": True, "note": ""}]})
        ]
    )

    nota = await CompletenessDimension(provider).evaluate(subject)

    assert "documentos exigidos" in provider.calls[0][0].content
    assert nota.evidence["expectations_declared"] is True


async def test_without_expectations_the_judge_decomposes_the_request() -> None:
    provider = FakeLLMProvider(
        script=[json.dumps({"items": [{"item": "prazo", "covered": True, "note": ""}]})]
    )

    nota = await CompletenessDimension(provider).evaluate(rag_subject())

    assert nota.evidence["expectations_declared"] is False


# ---------------------------------------------------------------- motor completo


async def test_a_judge_failure_does_not_fail_the_execution() -> None:
    """Cada dimensao e isolada pelo motor: o timeout de uma nao derruba as outras."""
    provider = FakeLLMProvider(script=[LLMTimeoutError()])

    report = await build_quality_engine(provider).evaluate(rag_subject())

    grounding = next(d for d in report.dimensions if d.dimension == "grounding")
    assert grounding.applicable is False
    assert grounding.evidence["error_code"] == "llm_timeout"


def test_without_a_provider_the_engine_is_free() -> None:
    """E o motor que a CI usa: mede confiabilidade sobre um historico inteiro sem pagar."""
    engine = build_quality_engine(None)

    assert engine.uses_llm is False
    assert engine.dimension_names == ["api_reliability"]


def test_with_a_provider_all_five_dimensions_are_present() -> None:
    engine = build_quality_engine(FakeLLMProvider())

    assert set(engine.dimension_names) == {
        "grounding",
        "relevance",
        "completeness",
        "consistency",
        "api_reliability",
    }
