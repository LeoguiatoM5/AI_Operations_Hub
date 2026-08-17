"""O conjunto de avaliacao: perguntas com o que se espera delas.

Cada entrada e uma afirmacao sobre como o sistema *deveria* se comportar, escrita antes de
saber como ele se comporta. E o que separa medicao de justificativa: um conjunto montado
depois de ver os resultados vira uma colecao de casos que ja passam.

**Toda entrada carrega um `note`.** Nao e documentacao de cortesia: um caso cujo motivo de
existir ninguem lembra e um caso que sera apagado no primeiro dia em que der trabalho.
"""

import json
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

from app.core.exceptions import ValidationError
from app.quality.base import Expectations


class EvalCase(BaseModel):
    """Uma pergunta e o que se espera da resposta."""

    id: str = Field(min_length=1, max_length=64)
    question: str = Field(min_length=1, max_length=1000)
    note: str = Field(
        min_length=1,
        max_length=500,
        description="Por que este caso existe. Obrigatorio.",
    )

    #: Assuntos que a resposta precisa cobrir. Alimentam `completeness` -- e la eles
    #: substituem a decomposicao feita pelo proprio modelo.
    expected_topics: list[str] = Field(default_factory=list, max_length=15)
    #: Nomes de arquivo que deveriam ser citados. Verificacao deterministica: nao precisa
    #: de juiz para comparar dois conjuntos de strings.
    expected_sources: list[str] = Field(default_factory=list, max_length=10)
    #: Afirmacoes que a resposta NAO pode conter. Sao as alucinacoes que ja vimos, ou as
    #: que o assunto convida.
    forbidden_claims: list[str] = Field(default_factory=list, max_length=10)
    #: `False` quando a resposta correta e "a base nao cobre isto". O caso mais valioso do
    #: conjunto: mede honestidade, que e o que um sistema de RAG erra primeiro.
    should_answer: bool = True

    @model_validator(mode="after")
    def a_refusal_case_expects_no_sources(self) -> "EvalCase":
        """Coerencia interna, na mesma linha do ED-028.

        Esperar que o sistema recuse E que cite fontes e contraditorio: se ha fonte a
        citar, havia cobertura, e a recusa estaria errada.
        """
        if not self.should_answer and self.expected_sources:
            raise ValueError(
                f"caso {self.id!r}: should_answer=false nao pode exigir expected_sources"
            )
        return self

    def expectations(self) -> Expectations:
        """Converte para o que o motor de qualidade entende."""
        return Expectations(
            expected_topics=list(self.expected_topics),
            expected_sources=list(self.expected_sources),
            forbidden_claims=list(self.forbidden_claims),
            should_answer=self.should_answer,
        )


def load_dataset(path: Path) -> list[EvalCase]:
    """Le e valida o conjunto inteiro.

    Falha na primeira entrada invalida, e nao ao final: um conjunto com uma linha quebrada
    produziria um relatorio silenciosamente incompleto, e a comparacao com o relatorio
    anterior passaria a ser entre coisas diferentes.
    """
    if not path.exists():
        raise ValidationError(
            f"Conjunto de avaliacao nao encontrado: {path}", details={"path": str(path)}
        )

    dados = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(dados, list):
        raise ValidationError("O conjunto de avaliacao precisa ser uma lista de objetos.")

    casos = [EvalCase.model_validate(item) for item in dados]

    identificadores = [caso.id for caso in casos]
    repetidos = {item for item in identificadores if identificadores.count(item) > 1}
    if repetidos:
        # Ids repetidos quebrariam a comparacao entre relatorios: dois casos diferentes
        # apareceriam como o mesmo, e a regressao de um seria escondida pelo outro.
        raise ValidationError(
            f"Ids repetidos no conjunto de avaliacao: {sorted(repetidos)}",
            details={"duplicated": sorted(repetidos)},
        )

    return casos
