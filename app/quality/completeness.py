"""Dimensao de completude: cobriu todos os itens do pedido?

E a dimensao que mais muda entre os dois modos, e por isso a que melhor mostra por que o
motor e um so.

**Offline**, `expected_topics` vem do conjunto de avaliacao -- escritos por uma pessoa. O
juiz nao decide o que era exigido, so verifica cobertura item a item. E a medicao mais
confiavel que o projeto tem, porque a lista do que se espera e verdade externa ao modelo.

**Online**, ninguem sabe de antemao o que o pedido exigia. O juiz decompoe o pedido ele
mesmo, e a nota carrega um vies inevitavel: o mesmo modelo que respondeu ajuda a definir o
que seria uma resposta completa. Menos confiavel -- e ainda assim util, porque pega o caso
grosseiro de responder metade do que foi pedido.

O prompt e o mesmo. A diferenca e um bloco preenchido ou vazio, e a evidencia registra
qual dos dois caminhos rodou.
"""

from pydantic import BaseModel, Field

from app.quality.base import DimensionScore, QualitySubject
from app.quality.judge import JudgedDimension, ratio, refusal_is_not_graded

MAX_ANSWER_CHARS = 4_000
MAX_ITEMS = 15


class ItemCoverage(BaseModel):
    """Um item do pedido e se a resposta o cobre."""

    item: str = Field(max_length=300)
    covered: bool
    note: str = Field(default="", max_length=300)


class CompletenessVerdict(BaseModel):
    """O que o juiz devolve."""

    items: list[ItemCoverage] = Field(default_factory=list, max_length=MAX_ITEMS)


class CompletenessDimension(JudgedDimension):
    """Verifica a cobertura do pedido, item a item."""

    @property
    def name(self) -> str:
        return "completeness"

    async def evaluate(self, subject: QualitySubject) -> DimensionScore:
        if not subject.answer.strip():
            return DimensionScore(
                dimension=self.name,
                score=0.0,
                applicable=False,
                reason="Nao houve resposta a avaliar.",
            )

        if not subject.answered:
            return refusal_is_not_graded(self.name)

        esperados = self._expected(subject)
        veredito, response = await self._judge(
            "completeness",
            CompletenessVerdict,
            task=subject.task,
            answer=subject.answer[:MAX_ANSWER_CHARS],
            expected_topics=(
                "\n".join(f"- {item}" for item in esperados)
                if esperados
                else "(nenhum item declarado -- decomponha o pedido voce mesmo)"
            ),
        )

        itens = veredito.items
        if not itens:
            return DimensionScore(
                dimension=self.name,
                score=0.0,
                applicable=False,
                reason="Nao foi possivel decompor o pedido em itens verificaveis.",
                cost_usd=response.cost_usd,
                tokens=response.usage.total_tokens,
            )

        cobertos = [item for item in itens if item.covered]
        faltando = [item for item in itens if not item.covered]

        return DimensionScore(
            dimension=self.name,
            score=ratio(len(cobertos), len(itens)),
            reason=self._describe(cobertos, faltando, itens),
            evidence={
                "items": len(itens),
                "covered": len(cobertos),
                "missing": [item.item for item in faltando[:5]],
                # Registra qual dos dois caminhos rodou: uma nota derivada de itens
                # escritos por uma pessoa vale mais que uma derivada da decomposicao do
                # proprio modelo, e quem le o relatorio precisa saber qual foi.
                "expectations_declared": bool(esperados),
            },
            cost_usd=response.cost_usd,
            tokens=response.usage.total_tokens,
        )

    @staticmethod
    def _expected(subject: QualitySubject) -> list[str]:
        if subject.expectations is None:
            return []
        return subject.expectations.expected_topics[:MAX_ITEMS]

    @staticmethod
    def _describe(
        cobertos: list[ItemCoverage], faltando: list[ItemCoverage], itens: list[ItemCoverage]
    ) -> str:
        if not faltando:
            return f"Os {len(cobertos)} itens do pedido foram cobertos."
        nomes = "; ".join(item.item[:80] for item in faltando[:3])
        return f"{len(faltando)} de {len(itens)} itens nao foram cobertos: {nomes}"
