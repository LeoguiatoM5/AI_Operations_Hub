"""Dimensao de fundamentacao: toda afirmacao tem fonte entre os trechos citados?

**A dimensao critica do motor.** Ela reprova sozinha (ver `CRITICAL_DIMENSIONS`), porque
afirmar sem fonte e o modo de falha mais caro de um sistema de RAG: a resposta parece
certa, soa fluente, e nao e verificavel. Os outros defeitos -- responder fora do assunto,
cobrir metade do pedido -- sao visiveis para quem le. Este nao e.

Tres casos sao resolvidos **sem** chamar o LLM, e cada um economiza uma chamada paga:

1. a resposta nao se apoia em base de conhecimento (analise de dados fornecidos) --
   inaplicavel, nao ha o que fundamentar;
2. o sistema declarou nao ter cobertura -- inaplicavel, recusar corretamente e o certo;
3. o sistema afirmou algo e nao citou nenhuma fonte -- nota zero, e nao precisa de juiz
   para saber disso.
"""

from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.quality.base import DimensionScore, QualitySubject
from app.quality.judge import JudgedDimension, ratio

logger = get_logger(__name__)

#: Teto de afirmacoes enviadas ao juiz. Cada uma custa tokens, e um relatorio alucinado
#: com cinquenta pontos-chave nao deve multiplicar por cinquenta a conta da avaliacao.
MAX_CLAIMS = 20
#: Teto de caracteres por trecho no prompt. Trechos ja chegam cortados em 300 do no de
#: pesquisa; o limite existe para o caso de um subject vir de outra origem.
MAX_SOURCE_CHARS = 600


class ClaimVerdict(BaseModel):
    """O veredito sobre uma afirmacao isolada."""

    claim: str = Field(max_length=1000)
    supported: bool
    source_index: int | None = Field(
        default=None, ge=1, description="Numero do trecho que sustenta, comecando em 1."
    )
    note: str = Field(default="", max_length=400)


class GroundingVerdict(BaseModel):
    """O que o juiz devolve."""

    verdicts: list[ClaimVerdict] = Field(default_factory=list, max_length=MAX_CLAIMS)


class GroundingDimension(JudgedDimension):
    """Verifica cada afirmacao contra os trechos citados."""

    @property
    def name(self) -> str:
        return "grounding"

    async def evaluate(self, subject: QualitySubject) -> DimensionScore:
        atalho = self._shortcut(subject)
        if atalho is not None:
            return atalho

        afirmacoes = self._claims(subject)
        if not afirmacoes:
            return DimensionScore(
                dimension=self.name,
                score=0.0,
                applicable=False,
                reason="A resposta nao contem afirmacoes verificaveis.",
            )

        veredito, response = await self._judge(
            "grounding",
            GroundingVerdict,
            task=subject.task,
            sources=self._render_sources(subject),
            claims=self._render_claims(afirmacoes),
        )

        # So contam os vereditos sobre afirmacoes que de fato enviamos. Um juiz que
        # inventa uma afirmacao inexistente e a aprova inflaria a nota -- e ja aconteceu
        # com modelos menores em tarefas de listagem.
        enviadas = {texto.strip() for texto in afirmacoes}
        validos = [item for item in veredito.verdicts if item.claim.strip() in enviadas]
        ignorados = len(veredito.verdicts) - len(validos)

        sustentadas = [item for item in validos if item.supported]
        sem_fonte = [item for item in validos if not item.supported]
        nota = ratio(len(sustentadas), len(validos)) if validos else 0.0

        if ignorados:
            logger.warning(
                "grounding_verdicts_discarded",
                discarded=ignorados,
                sent=len(afirmacoes),
                returned=len(veredito.verdicts),
            )

        return DimensionScore(
            dimension=self.name,
            score=nota,
            reason=self._describe(sustentadas, sem_fonte, validos),
            evidence={
                "claims_checked": len(validos),
                "supported": len(sustentadas),
                "unsupported": [{"claim": item.claim, "note": item.note} for item in sem_fonte[:5]],
                "sources_available": len(subject.sources),
                "verdicts_discarded": ignorados,
            },
            cost_usd=response.cost_usd,
            tokens=response.usage.total_tokens,
        )

    # ------------------------------------------------------------------ atalhos

    def _shortcut(self, subject: QualitySubject) -> DimensionScore | None:
        """Casos decididos sem gastar uma chamada paga."""
        if not subject.source_based:
            return DimensionScore(
                dimension=self.name,
                score=0.0,
                applicable=False,
                reason="A resposta nao deriva da base de conhecimento: nada a fundamentar.",
            )

        if not subject.answered:
            return DimensionScore(
                dimension=self.name,
                score=0.0,
                applicable=False,
                reason="O sistema declarou nao ter cobertura para responder -- nao afirmou nada.",
            )

        if not subject.has_sources:
            # Aqui o defeito e evidente: afirmou apoiado na base e nao citou trecho
            # nenhum. Perguntar a um juiz seria pagar para confirmar o obvio.
            return DimensionScore(
                dimension=self.name,
                score=0.0,
                reason="A resposta afirma derivar da base, mas nao citou nenhuma fonte.",
                evidence={"claims_checked": 0, "sources_available": 0},
            )

        return None

    # ------------------------------------------------------------------ apoio

    @staticmethod
    def _claims(subject: QualitySubject) -> list[str]:
        """Afirmacoes a verificar.

        Prefere `claims` (o relatorio ja vem quebrado em pontos-chave). Sem eles, cai
        para a resposta inteira como uma afirmacao unica -- pior granularidade, mas
        melhor que nao medir.
        """
        if subject.claims:
            return [texto.strip() for texto in subject.claims[:MAX_CLAIMS] if texto.strip()]
        return [subject.answer.strip()] if subject.answer.strip() else []

    @staticmethod
    def _render_sources(subject: QualitySubject) -> str:
        return "\n\n".join(
            f"[{numero}] ({fonte.filename or fonte.document_id})\n"
            f"{fonte.excerpt[:MAX_SOURCE_CHARS]}"
            for numero, fonte in enumerate(subject.sources, start=1)
        )

    @staticmethod
    def _render_claims(afirmacoes: list[str]) -> str:
        return "\n".join(f"- {texto}" for texto in afirmacoes)

    @staticmethod
    def _describe(
        sustentadas: list[ClaimVerdict], sem_fonte: list[ClaimVerdict], validos: list[ClaimVerdict]
    ) -> str:
        if not validos:
            return "O juiz nao avaliou nenhuma das afirmacoes enviadas."
        if not sem_fonte:
            return f"As {len(sustentadas)} afirmacoes estao sustentadas pelos trechos citados."

        exemplos = "; ".join(f'"{item.claim[:120]}"' for item in sem_fonte[:3])
        return (
            f"{len(sem_fonte)} de {len(validos)} afirmacoes nao tem sustentacao nos trechos "
            f"citados: {exemplos}"
        )
