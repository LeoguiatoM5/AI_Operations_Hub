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


def _squash(texto: str) -> str:
    """Normaliza espacos e caixa para comparar afirmacoes."""
    return " ".join(texto.lower().split())


#: A partir deste tamanho, contencao de texto e evidencia de correspondencia. Abaixo
#: dele, nao e: "a" esta contido em "inventada", e aceitar isso deixaria passar
#: exatamente a afirmacao alucinada que este filtro existe para barrar.
MIN_CONTAINMENT_CHARS = 40


def _matches_any(devolvida: str, enviadas: list[str]) -> bool:
    """A afirmacao devolvida pelo juiz corresponde a alguma que enviamos?

    A comparacao era igualdade exata, e o conjunto de avaliacao mostrou que isso e fragil:
    quando a afirmacao e uma resposta inteira, o juiz reescreve pontuacao ou corta o final,
    e o veredito legitimo era descartado -- derrubando a nota a zero por uma diferenca de
    espaco em branco.

    Agora normaliza caixa e espacos e, **apenas para textos longos**, aceita contencao nos
    dois sentidos, que cobre o truncamento. O limite de tamanho nao e detalhe: sem ele, a
    primeira versao desta funcao passou a aceitar uma afirmacao inventada de nove
    caracteres porque uma das enviadas tinha um caractere -- o teste pegou.
    """
    alvo = _squash(devolvida)
    if not alvo:
        return False

    for item in enviadas:
        candidato = _squash(item)
        if alvo == candidato:
            return True
        longos = len(alvo) >= MIN_CONTAINMENT_CHARS and len(candidato) >= MIN_CONTAINMENT_CHARS
        if longos and (alvo in candidato or candidato in alvo):
            return True
    return False


def _pair_verdicts(
    devolvidos: list["ClaimVerdict"], enviadas: list[str]
) -> tuple[list["ClaimVerdict"], int]:
    """Associa cada veredito a afirmacao que ele julga.

    **Quando as contagens batem, vale a posicao.** O prompt pede um veredito por afirmacao,
    na ordem; se vieram tantos quantos foram enviados, a leitura natural e a posicional, e
    o texto devolvido e apenas o eco -- que o modelo reescreve com liberdade. O texto da
    afirmacao e substituido pelo nosso, para que a evidencia mostre o que realmente foi
    julgado.

    **Quando nao batem**, cai para correspondencia por texto: e o unico jeito de saber
    quais vereditos aproveitar, e o excedente e descartado.

    Isto nasceu de um falso negativo grave encontrado pelo conjunto de avaliacao: duas
    respostas quase literais do documento receberam `grounding = 0` porque o juiz devolveu
    o texto da afirmacao com pequenas diferencas e o filtro descartou o veredito legitimo.
    Como `grounding` reprova sozinha, o defeito rejeitaria respostas corretas em producao.
    """
    if len(devolvidos) == len(enviadas):
        return (
            [
                item.model_copy(update={"claim": texto})
                for item, texto in zip(devolvidos, enviadas, strict=True)
            ],
            0,
        )

    validos = [item for item in devolvidos if _matches_any(item.claim, enviadas)]
    return validos, len(devolvidos) - len(validos)


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
            # A pergunta do usuario NAO e enviada, de proposito. Enviando-a, o juiz
            # deslizava de "o trecho diz isto?" para "isto responde bem a pergunta?" -- e
            # reprovava afirmacoes literalmente presentes no trecho, com notas que
            # confirmavam a fonte e vereditos que a negavam. Fundamentacao e uma relacao
            # entre afirmacao e trecho; a pergunta nao participa dela.
            task="Verifique cada afirmacao contra os trechos fornecidos.",
            sources=self._render_sources(subject),
            claims=self._render_claims(afirmacoes),
        )

        validos, ignorados = _pair_verdicts(veredito.verdicts, afirmacoes)

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
