"""Dimensao de confiabilidade da execucao.

A unica das cinco que **nao custa nada**: os dados ja estao gravados em
`agent_executions` desde o V1. Nenhuma chamada de LLM, nenhuma latencia adicional.

E por isso ela e a primeira a existir. Ela prova o motor inteiro -- contrato, agregacao,
gravacao do score -- sem gastar um centavo, e as dimensoes por LLM entram depois num
esqueleto ja testado.

**O que ela NAO mede.** Se a resposta esta certa. Uma execucao pode ter rodado sem um
unico erro e produzir uma resposta alucinada; esta dimensao daria 1.0. E o motivo de as
outras quatro existirem -- e de o agregado ser uma media, nao um portao unico.
"""

from app.core.logging import get_logger
from app.quality.base import DimensionScore, QualitySubject, StepFacts

logger = get_logger(__name__)

#: Um passo que falhou custa a nota inteira daquele passo.
FAILURE_WEIGHT = 1.0
#: Um passo que precisou repetir custa menos: ele terminou certo, mas pagou o dobro em
#: tokens e latencia, e um sistema que so acerta na segunda tentativa e fragil.
RETRY_WEIGHT = 0.35

#: Numeros de partida, nao verdades. Saem da medicao com o conjunto de avaliacao (V5.2),
#: pela mesma disciplina do `min_relevant_score` (ED-038): valor arbitrado se declara
#: arbitrado ate alguem medir.


class ApiReliabilityDimension:
    """Houve erro, timeout ou repeticao no caminho?"""

    @property
    def name(self) -> str:
        return "api_reliability"

    @property
    def uses_llm(self) -> bool:
        return False

    async def evaluate(self, subject: QualitySubject) -> DimensionScore:
        passos = subject.steps
        if not passos:
            # Sem passos registrados nao ha o que medir. Acontece no modo offline, quando
            # a entrada do dataset descreve so a pergunta e a resposta esperada.
            return DimensionScore(
                dimension=self.name,
                score=0.0,
                applicable=False,
                reason="A execucao nao registrou passos de agente.",
            )

        falhas = [passo for passo in passos if not passo.succeeded]
        # `attempts` conta a tentativa original; so o excedente e repeticao.
        repeticoes = sum(passo.attempts - 1 for passo in passos)

        total = len(passos)
        penalidade = (FAILURE_WEIGHT * len(falhas) + RETRY_WEIGHT * repeticoes) / total
        nota = max(0.0, min(1.0, 1.0 - penalidade))

        return DimensionScore(
            dimension=self.name,
            score=round(nota, 4),
            reason=self._describe(falhas, repeticoes, total),
            evidence={
                "steps": total,
                "failed": len(falhas),
                "extra_attempts": repeticoes,
                "error_codes": sorted({p.error_code for p in falhas if p.error_code}),
                "failed_agents": sorted({p.agent for p in falhas}),
            },
        )

    @staticmethod
    def _describe(falhas: list[StepFacts], repeticoes: int, total: int) -> str:
        """Frase legivel por humano -- e util para o modelo no retry dirigido."""
        if not falhas and not repeticoes:
            return f"Os {total} passos rodaram sem erro nem repeticao."

        partes = []
        if falhas:
            agentes = ", ".join(sorted({p.agent for p in falhas}))
            codigos = ", ".join(sorted({p.error_code for p in falhas if p.error_code}))
            trecho = f"{len(falhas)} de {total} passos falharam ({agentes})"
            partes.append(f"{trecho}: {codigos}" if codigos else trecho)
        if repeticoes:
            partes.append(
                f"{repeticoes} tentativa(s) extra(s) foram necessarias para obter saida valida"
            )
        return ". ".join(partes) + "."
