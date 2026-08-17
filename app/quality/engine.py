"""Agregacao das dimensoes em uma nota so.

Tres decisoes moram aqui, e as tres sao sobre nao mentir com numero.

**Dimensao inaplicavel sai da conta.** Nao entra como zero. Uma resposta que corretamente
declarou nao ter cobertura nao tem fontes a checar -- pontuar isso como zero puniria o
comportamento certo e derrubaria o agregado por um motivo que nao existe.

**O agregado nao esconde a reprovacao pontual.** Uma media alta pode conviver com uma
dimensao no chao: o sistema respondeu no assunto, completo e coerente, e *inventou a
fonte*. Por isso `passed` exige as duas coisas -- media acima do limite E nenhuma
dimensao critica reprovada isoladamente.

**Falha ao medir nao reprova o que foi medido.** Se uma dimensao por LLM estourar, ela
vira inaplicavel com o motivo registrado, e o motor segue. Um portao de qualidade que
derruba a resposta porque o proprio portao quebrou inverte a razao de existir.
"""

import asyncio
from collections.abc import Sequence

from app.core.exceptions import AIHubError
from app.core.logging import get_logger
from app.quality.base import Dimension, DimensionScore, QualityReport, QualitySubject

logger = get_logger(__name__)

#: Peso de cada dimensao no agregado. `grounding` pesa mais porque afirmar sem fonte e o
#: modo de falha mais caro de um sistema de RAG: parece certo e nao e verificavel.
#: Como todo numero deste modulo, e ponto de partida ate a medicao com o dataset (V5.2).
DEFAULT_WEIGHTS: dict[str, float] = {
    "grounding": 2.0,
    "relevance": 1.5,
    "completeness": 1.0,
    "consistency": 1.0,
    "api_reliability": 0.5,
}

#: Dimensoes que reprovam sozinhas, por pior que seja a media das outras. Afirmar algo
#: que nenhuma fonte sustenta nao e compensavel por escrever bem sobre o assunto certo.
CRITICAL_DIMENSIONS = frozenset({"grounding"})

#: Peso de uma dimensao que ninguem declarou. Existir sem peso definido nao pode
#: significar "nao conta": seria um jeito silencioso de desligar uma medicao.
FALLBACK_WEIGHT = 1.0


class QualityEngine:
    """Roda as dimensoes sobre um subject e consolida o resultado."""

    def __init__(
        self,
        dimensions: Sequence[Dimension],
        *,
        threshold: float = 0.7,
        weights: dict[str, float] | None = None,
    ) -> None:
        """
        Args:
            threshold: nota minima do agregado para aprovar.
            weights: peso por dimensao. `None` usa `DEFAULT_WEIGHTS`.
        """
        self._dimensions = list(dimensions)
        self._threshold = threshold
        self._weights = weights if weights is not None else DEFAULT_WEIGHTS

    @property
    def dimension_names(self) -> list[str]:
        return [dimension.name for dimension in self._dimensions]

    @property
    def uses_llm(self) -> bool:
        """Se este motor gasta tokens. Um motor so de `api_reliability` nao gasta."""
        return any(dimension.uses_llm for dimension in self._dimensions)

    async def evaluate(self, subject: QualitySubject) -> QualityReport:
        """Mede todas as dimensoes e consolida.

        As dimensoes rodam em paralelo porque sao independentes -- nenhuma le o resultado
        da outra. Com quatro delas chamando LLM, medir em serie multiplicaria por quatro a
        latencia de um portao que roda ANTES de a resposta ser entregue.
        """
        notas = await asyncio.gather(
            *(self._safely(dimension, subject) for dimension in self._dimensions)
        )

        aplicaveis = [nota for nota in notas if nota.applicable]
        # Arredondado ANTES de comparar, e nao so na exibicao. Sem isso, uma media que
        # aparece como 0.7 contra um limite de 0.7 sai reprovada porque o float cru era
        # 0.6999999999999998 -- um relatorio que mostra um numero e decide por outro.
        agregado = round(self._aggregate(aplicaveis), 4)
        criticas = [
            nota.dimension
            for nota in aplicaveis
            if nota.dimension in CRITICAL_DIMENSIONS and nota.score < self._threshold
        ]

        report = QualityReport(
            score=agregado,
            passed=agregado >= self._threshold and not criticas,
            threshold=self._threshold,
            dimensions=list(notas),
            cost_usd=round(sum(nota.cost_usd for nota in notas), 8),
        )

        logger.info(
            "quality_evaluated",
            score=report.score,
            passed=report.passed,
            threshold=self._threshold,
            measured=len(aplicaveis),
            skipped=len(notas) - len(aplicaveis),
            critical_failures=criticas,
            cost_usd=report.cost_usd,
        )
        return report

    def _aggregate(self, aplicaveis: list[DimensionScore]) -> float:
        """Media ponderada. Sem nada aplicavel, a nota e 1.0.

        A escolha do 1.0 e consciente: nao medir nao e o mesmo que medir mal. Devolver 0.0
        faria toda execucao sem material avaliavel ser reprovada pelo portao -- e o
        `passed` do relatorio diria "reprovado" sobre algo que ninguem examinou. Quem
        precisa distinguir "aprovado" de "nao avaliado" consulta `dimensions`.
        """
        if not aplicaveis:
            return 1.0

        pesos = [self._weights.get(nota.dimension, FALLBACK_WEIGHT) for nota in aplicaveis]
        total = sum(pesos)
        if total <= 0:
            # Todos os pesos zerados: cai para media simples em vez de dividir por zero.
            return sum(nota.score for nota in aplicaveis) / len(aplicaveis)

        return sum(nota.score * peso for nota, peso in zip(aplicaveis, pesos, strict=True)) / total

    async def _safely(self, dimension: Dimension, subject: QualitySubject) -> DimensionScore:
        """Executa uma dimensao sem deixar que ela derrube a avaliacao inteira."""
        try:
            return await dimension.evaluate(subject)
        except AIHubError as error:
            logger.warning(
                "quality_dimension_failed",
                dimension=dimension.name,
                error_code=error.code,
                error_message=error.message,
            )
            return DimensionScore(
                dimension=dimension.name,
                score=0.0,
                applicable=False,
                reason=f"Nao foi possivel medir esta dimensao: {error.message}",
                evidence={"error_code": error.code},
            )
