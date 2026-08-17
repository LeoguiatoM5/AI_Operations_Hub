"""Dimensao de pertinencia: a resposta trata da pergunta feita?

A mais simples das quatro, e a que mais depende de o prompt separar duas coisas que se
confundem com facilidade: **pertinencia nao e qualidade**. Uma resposta curta, incompleta
ou ate errada pode ser perfeitamente pertinente -- e medir as duas coisas na mesma nota
tornaria impossivel saber qual delas reprovou.

Uma recusa honesta conta como pertinente. "A base nao cobre este assunto" responde ao que
foi perguntado; puni-la aqui empurraria o sistema a inventar em vez de recusar, que e o
oposto do que o projeto inteiro tenta garantir.
"""

from pydantic import BaseModel, Field

from app.quality.base import DimensionScore, QualitySubject
from app.quality.judge import JudgedDimension

#: Desconto por trecho fora do assunto numa resposta que, no geral, responde ao pedido.
#: Divagar nao invalida a resposta, mas dilui: quem le precisa garimpar.
OFF_TOPIC_PENALTY = 0.15
MAX_ANSWER_CHARS = 4_000


class RelevanceVerdict(BaseModel):
    """O que o juiz devolve."""

    addresses_request: bool
    off_topic: list[str] = Field(default_factory=list, max_length=10)
    reason: str = Field(default="", max_length=500)


class RelevanceDimension(JudgedDimension):
    """A resposta trata do que foi pedido?"""

    @property
    def name(self) -> str:
        return "relevance"

    async def evaluate(self, subject: QualitySubject) -> DimensionScore:
        if not subject.answer.strip():
            return DimensionScore(
                dimension=self.name,
                score=0.0,
                applicable=False,
                reason="Nao houve resposta a avaliar.",
            )

        veredito, response = await self._judge(
            "relevance",
            RelevanceVerdict,
            task=subject.task,
            answer=subject.answer[:MAX_ANSWER_CHARS],
        )

        if not veredito.addresses_request:
            nota = 0.0
        else:
            nota = max(0.0, 1.0 - OFF_TOPIC_PENALTY * len(veredito.off_topic))

        return DimensionScore(
            dimension=self.name,
            score=round(nota, 4),
            reason=veredito.reason
            or ("A resposta trata do pedido." if nota else "Fora do assunto."),
            evidence={
                "addresses_request": veredito.addresses_request,
                "off_topic": veredito.off_topic[:5],
            },
            cost_usd=response.cost_usd,
            tokens=response.usage.total_tokens,
        )
