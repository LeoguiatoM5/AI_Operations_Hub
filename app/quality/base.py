"""Contratos do motor de qualidade.

Tres tipos carregam o desenho inteiro:

- `QualitySubject` -- o que esta sendo avaliado, em forma pura. Nao conhece banco, HTTP
  nem grafo. E ele que permite ao mesmo motor rodar sobre uma execucao real e sobre uma
  entrada do conjunto de avaliacao.
- `Dimension` -- um Protocol, como os demais pontos de extensao do projeto. Cada dimensao
  responde UMA pergunta e devolve uma nota justificada.
- `QualityReport` -- o agregado, com a nota de cada dimensao preservada.

**Por que preservar a nota de cada dimensao.** Um numero unico diz "0.62" e nao diz o que
fazer. As notas por dimensao dizem *qual* pergunta o sistema respondeu mal -- e e isso que
alimenta o retry dirigido, pelo mesmo raciocinio do ED-023: informar o motivo exato tem
chance muito maior de corrigir do que repetir o pedido.
"""

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field, model_validator


class CitedSource(BaseModel):
    """Um trecho que a resposta afirma ter usado como fonte."""

    document_id: str = ""
    filename: str | None = None
    excerpt: str = ""
    score: float | None = Field(default=None, description="Similaridade da recuperacao.")


class StepFacts(BaseModel):
    """O que aconteceu em um passo da execucao.

    Copia enxuta de `AgentExecution`, e nao o modelo do SQLAlchemy: a dimensao de
    confiabilidade precisa apenas destes campos, e depender do ORM tornaria o motor
    impossivel de testar sem banco -- alem de amarra-lo a um esquema que muda por motivos
    que nada tem a ver com qualidade.
    """

    agent: str
    action: str
    succeeded: bool
    attempts: int = Field(default=1, ge=1)
    error_code: str | None = None


class Expectations(BaseModel):
    """O que se espera da resposta. Existe apenas no modo offline.

    E a unica diferenca real entre os dois modos: em producao ninguem sabe a resposta
    certa de antemao -- se soubesse, nao precisaria do sistema. No conjunto de avaliacao,
    sim, e e isso que torna a medicao mais afiada la.

    As dimensoes que dependem disto se declaram **inaplicaveis** quando falta, em vez de
    inventar uma nota. Ver `DimensionScore.applicable`.
    """

    expected_topics: list[str] = Field(
        default_factory=list, description="Assuntos que a resposta precisa cobrir."
    )
    expected_sources: list[str] = Field(
        default_factory=list, description="Documentos que deveriam ter sido citados."
    )
    forbidden_claims: list[str] = Field(
        default_factory=list,
        description="Afirmacoes que a resposta NAO pode conter -- alucinacoes conhecidas.",
    )
    should_answer: bool = Field(
        default=True,
        description="False quando a resposta correta e 'a base nao cobre isto'.",
    )

    @property
    def is_empty(self) -> bool:
        return not (self.expected_topics or self.expected_sources or self.forbidden_claims)


class QualitySubject(BaseModel):
    """O que esta sendo avaliado.

    Deliberadamente pobre em tipos: strings e listas. Um `QualitySubject` construido a
    partir de uma execucao do banco e outro construido a partir de uma linha de JSON
    precisam ser indistinguiveis para as dimensoes -- e serao, porque nao ha nada aqui que
    so uma das duas origens saiba preencher.
    """

    task: str = Field(description="O que foi pedido, em linguagem natural.")
    answer: str = Field(default="", description="O texto entregue ao usuario.")
    #: Afirmacoes isoladas, quando existem. O relatorio traz `key_points`; a resposta de
    #: RAG traz uma frase so. Quem constroi o subject decide como quebrar.
    claims: list[str] = Field(default_factory=list)
    sources: list[CitedSource] = Field(default_factory=list)
    #: `False` quando o sistema declarou nao ter cobertura para responder. Nao e falha:
    #: e a resposta honesta, e a dimensao de grounding precisa saber disso para nao punir
    #: uma recusa correta.
    answered: bool = True
    #: `True` quando a resposta afirma derivar de trechos recuperados da base. Distingue
    #: dois casos que `sources=[]` nao separa sozinho: uma analise de dados fornecidos
    #: pelo usuario (que nao tem fontes e nao deveria ter) e uma resposta de RAG que
    #: afirmou algo sem citar nada (que e o pior defeito possivel num sistema de RAG).
    source_based: bool = False
    steps: list[StepFacts] = Field(default_factory=list)
    expectations: Expectations | None = None

    @property
    def has_sources(self) -> bool:
        return bool(self.sources)


class DimensionScore(BaseModel):
    """A nota de uma dimensao, com a justificativa junto.

    `reason` nao e enfeite: e o texto que volta para o modelo no retry dirigido e o que
    aparece no relatorio de evals. Uma nota sem motivo obriga quem le a reabrir o caso
    para descobrir o que houve.
    """

    dimension: str
    score: float = Field(ge=0.0, le=1.0)
    #: `False` quando a dimensao nao tinha o que medir (sem fontes para checar grounding,
    #: sem expectativas declaradas). Nota zero seria pior que nada: puniria o sistema por
    #: um caso em que ele nao errou -- e derrubaria o agregado por um motivo inexistente.
    applicable: bool = True
    reason: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)
    #: Custo de MEDIR. Dimensao por LLM nao e gratuita, e um portao de qualidade que
    #: dobra a conta precisa mostrar isso em vez de esconder.
    cost_usd: float = Field(default=0.0, ge=0.0)
    tokens: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def inapplicable_has_no_score(self) -> "DimensionScore":
        """Inaplicavel com nota alta enganaria tanto quanto com nota baixa."""
        if not self.applicable and self.score != 0.0:
            raise ValueError("dimensao inaplicavel nao deve carregar nota: use score=0.0")
        return self


class QualityReport(BaseModel):
    """Resultado da avaliacao completa."""

    score: float = Field(ge=0.0, le=1.0, description="Media ponderada das aplicaveis.")
    passed: bool
    threshold: float = Field(ge=0.0, le=1.0)
    dimensions: list[DimensionScore] = Field(default_factory=list)
    cost_usd: float = Field(default=0.0, ge=0.0)

    @property
    def applicable(self) -> list[DimensionScore]:
        return [item for item in self.dimensions if item.applicable]

    @property
    def failures(self) -> list[DimensionScore]:
        """Dimensoes aplicaveis abaixo do limite, da pior para a menos ruim."""
        return sorted(
            (item for item in self.applicable if item.score < self.threshold),
            key=lambda item: item.score,
        )

    def feedback(self) -> str:
        """Texto acionavel sobre o que reprovou.

        E isto que vai para o modelo no retry dirigido -- e para o relatorio de evals.
        """
        if not self.failures:
            return ""
        return "\n".join(
            f"- {item.dimension} ({item.score:.2f}): {item.reason}" for item in self.failures
        )


@runtime_checkable
class Dimension(Protocol):
    """Uma pergunta que o motor sabe fazer sobre uma resposta."""

    @property
    def name(self) -> str: ...

    @property
    def uses_llm(self) -> bool:
        """Se medir esta dimensao custa uma chamada paga.

        Exposto no contrato porque o chamador precisa poder montar um motor barato: o
        modo offline roda sobre dezenas de entradas, e nem toda execucao justifica pagar
        por todas as dimensoes.
        """
        ...

    async def evaluate(self, subject: QualitySubject) -> DimensionScore:
        """Mede e devolve a nota. Nao levanta excecao por falta de dados.

        Ausencia de material para medir e `applicable=False`, nao erro: o portao de
        qualidade nao pode derrubar uma execucao que deu certo so porque ele proprio nao
        tinha o que avaliar.
        """
        ...
