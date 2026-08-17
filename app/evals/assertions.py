"""Verificacoes deterministicas sobre uma resposta.

**A parte da avaliacao que nao precisa de LLM** -- e a mais confiavel que existe aqui,
justamente por isso. Comparar dois conjuntos de nomes de arquivo nao tem vies, nao tem
custo e nao muda de resultado entre execucoes.

As dimensoes julgadas dao nota; estas dao veredito. Um relatorio de evals em que as
assercoes passam e as notas caem indica um sistema que acerta o essencial e escreve mal.
O contrario -- notas altas com assercoes falhando -- indica um juiz complacente, e e o
sinal mais importante que o conjunto pode dar.
"""

import unicodedata

from pydantic import BaseModel, Field

from app.evals.dataset import EvalCase


class AssertionResult(BaseModel):
    """O resultado de uma verificacao deterministica."""

    name: str
    passed: bool
    detail: str = Field(default="", max_length=600)


def normalize(texto: str) -> str:
    """Minusculas sem acento, para comparacao de texto.

    A busca por afirmacao proibida falharia por acento ou caixa sem isto -- e uma
    alucinacao escrita com maiuscula continua sendo uma alucinacao.
    """
    decomposto = unicodedata.normalize("NFKD", texto.lower())
    return "".join(ch for ch in decomposto if not unicodedata.combining(ch))


def check_answered(case: EvalCase, *, answered: bool) -> AssertionResult:
    """O sistema respondeu quando deveria -- e recusou quando deveria recusar.

    A verificacao mais valiosa do conjunto. Um sistema de RAG erra primeiro pela
    complacencia: responde o que nao sabe, com a mesma fluencia com que responde o que
    sabe. Aqui isso e um booleano contra um booleano.
    """
    ok = answered == case.should_answer
    if ok:
        detalhe = "Respondeu." if answered else "Recusou, como esperado."
    elif case.should_answer:
        detalhe = "Deveria ter respondido a partir da base, e recusou."
    else:
        detalhe = "Deveria ter recusado por falta de cobertura, e respondeu."
    return AssertionResult(name="answered_as_expected", passed=ok, detail=detalhe)


def check_sources(case: EvalCase, *, cited: list[str]) -> AssertionResult:
    """As fontes esperadas foram citadas.

    Compara por nome de arquivo, e exige **contencao**, nao igualdade: citar um documento
    a mais nao e erro -- pode haver mais de um trecho pertinente, e exigir o conjunto
    exato transformaria uma resposta melhor em reprovacao.
    """
    if not case.expected_sources:
        return AssertionResult(
            name="expected_sources_cited", passed=True, detail="Nenhuma fonte exigida."
        )

    citadas = {normalize(nome) for nome in cited}
    faltando = [nome for nome in case.expected_sources if normalize(nome) not in citadas]

    return AssertionResult(
        name="expected_sources_cited",
        passed=not faltando,
        detail=(
            f"Citou {sorted(citadas)}."
            if not faltando
            else f"Faltou citar: {faltando}. Citou: {sorted(citadas)}."
        ),
    )


def check_forbidden(case: EvalCase, *, answer: str) -> AssertionResult:
    """Nenhuma afirmacao proibida aparece na resposta.

    Busca por substring normalizada. **E uma verificacao grosseira, e isso e deliberado**:
    ela pega a alucinacao literal que ja observamos, sem custo e sem ambiguidade. A
    parafrase escapa -- e para isso existe `grounding`, que le os trechos. As duas se
    complementam justamente porque falham em lugares diferentes.
    """
    if not case.forbidden_claims:
        return AssertionResult(
            name="no_forbidden_claims", passed=True, detail="Nenhuma afirmacao proibida declarada."
        )

    texto = normalize(answer)
    encontradas = [item for item in case.forbidden_claims if normalize(item) in texto]

    return AssertionResult(
        name="no_forbidden_claims",
        passed=not encontradas,
        detail=("Nenhuma encontrada." if not encontradas else f"Encontradas: {encontradas}."),
    )


def run_assertions(
    case: EvalCase, *, answered: bool, answer: str, cited: list[str]
) -> list[AssertionResult]:
    """Todas as verificacoes deterministicas de um caso."""
    return [
        check_answered(case, answered=answered),
        check_sources(case, cited=cited),
        check_forbidden(case, answer=answer),
    ]
