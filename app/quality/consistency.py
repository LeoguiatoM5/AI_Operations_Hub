"""Dimensao de coerencia: ha contradicao interna ou entre agentes?

E a dimensao que existe por causa da arquitetura. Num sistema de agente unico, coerencia
interna e quase gratuita -- o mesmo modelo escreve tudo de uma vez. Aqui a pesquisa, a
analise e o relatorio sao chamadas separadas, com contextos parciais: o relatorio pode
afirmar com confianca algo que a analise disse nao ter conseguido apurar, e nada nas
validacoes de schema pega isso.

O `TriageResult.approval_implies_an_automation_step` (ED-028) faz a mesma coisa dentro de
UM objeto. Esta dimensao e a versao entre objetos, onde o Pydantic nao alcanca.

**Contradicao exige citacao literal das duas partes.** Sem isso o modelo produz achados
plausiveis e infalsificaveis -- que sao piores que nenhum achado, porque parecem trabalho.
"""

import json
from typing import Any

from pydantic import BaseModel, Field

from app.quality.base import DimensionScore, QualitySubject
from app.quality.judge import JudgedDimension

#: Desconto por contradicao. Duas ja derrubam a dimensao abaixo do limite padrao de 0.7 --
#: deliberado: um material que se contradiz duas vezes nao deve passar por pouco.
CONTRADICTION_PENALTY = 0.34
MAX_MATERIAL_CHARS = 6_000


class Contradiction(BaseModel):
    """Duas partes do material que nao podem ser verdadeiras ao mesmo tempo."""

    statement_a: str = Field(max_length=500)
    statement_b: str = Field(max_length=500)
    explanation: str = Field(default="", max_length=400)


class ConsistencyVerdict(BaseModel):
    """O que o juiz devolve."""

    contradictions: list[Contradiction] = Field(default_factory=list, max_length=10)


class ConsistencyDimension(JudgedDimension):
    """Procura contradicoes no material produzido pelos agentes."""

    @property
    def name(self) -> str:
        return "consistency"

    async def evaluate(self, subject: QualitySubject) -> DimensionScore:
        material = self._render(subject)
        if not material.strip():
            return DimensionScore(
                dimension=self.name,
                score=0.0,
                applicable=False,
                reason="Nao ha material suficiente para procurar contradicoes.",
            )

        veredito, response = await self._judge(
            "consistency",
            ConsistencyVerdict,
            # A pergunta do usuario NAO e enviada -- mesma correcao do ED-078, que eu
            # havia aplicado so no grounding. Recebendo-a, o juiz a tratou como uma
            # AFIRMACAO e acusou contradicao entre a pergunta e a resposta: uma recusa
            # correta ("os trechos nao mencionam essa possibilidade") virou "contradiz" a
            # pergunta "posso pedir excecao?".
            #
            # Coerencia e uma propriedade do material consigo mesmo. Uma pergunta nao
            # afirma nada, e portanto nao pode contradizer coisa alguma.
            task="Procure contradicoes dentro do material abaixo.",
            material=material,
        )

        # Um achado sem as duas partes citadas e impressao, nao contradicao: descartado
        # antes de virar nota, como manda o proprio prompt.
        achados = [
            item
            for item in veredito.contradictions
            if item.statement_a.strip() and item.statement_b.strip()
        ]
        nota = max(0.0, 1.0 - CONTRADICTION_PENALTY * len(achados))

        return DimensionScore(
            dimension=self.name,
            score=round(nota, 4),
            reason=self._describe(achados),
            evidence={
                "contradictions": [
                    {"a": item.statement_a, "b": item.statement_b, "why": item.explanation}
                    for item in achados[:5]
                ],
                "discarded": len(veredito.contradictions) - len(achados),
            },
            cost_usd=response.cost_usd,
            tokens=response.usage.total_tokens,
        )

    @staticmethod
    def _render(subject: QualitySubject) -> str:
        """Monta o material que o juiz vai ler.

        Inclui as afirmacoes isoladas junto da resposta corrida: a contradicao mais comum
        aparece entre um ponto-chave e o texto do resumo, e ver os dois lado a lado e o
        que torna o conflito visivel.
        """
        blocos: dict[str, Any] = {"resposta": subject.answer[:MAX_MATERIAL_CHARS]}
        if subject.claims:
            blocos["afirmacoes"] = subject.claims
        if not subject.answered:
            blocos["observacao"] = "O sistema declarou nao ter cobertura para responder."
        return json.dumps(blocos, ensure_ascii=False, indent=2)

    @staticmethod
    def _describe(achados: list[Contradiction]) -> str:
        if not achados:
            return "Nao foram encontradas contradicoes no material."
        primeira = achados[0]
        return (
            f"{len(achados)} contradicao(oes) encontrada(s). Exemplo: "
            f'"{primeira.statement_a[:100]}" contra "{primeira.statement_b[:100]}"'
        )
