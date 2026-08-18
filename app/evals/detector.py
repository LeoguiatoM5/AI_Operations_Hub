"""Avaliacao do DETECTOR, e nao do sistema.

O conjunto principal (`evaluation_dataset.json`) roda o sistema e pergunta "a resposta
esta boa?". Este pergunta outra coisa: **"o motor de qualidade percebe quando ela esta
ruim?"**.

**Por que os dois nao podem ser o mesmo conjunto.** Para calibrar um limite e preciso
saber que nota uma resposta ruim tira -- e nao da para esperar o sistema errar sob
encomenda. Na ultima rodada ele acertou 15 de 16 casos com nota 1.00: nao ha um unico
exemplo entre 0.0 e 0.91, e um limite escolhido nesse vazio e chute com aparencia de
medicao.

A saida e escrever as respostas ruins a mao, cada uma com **um defeito conhecido**, e
verificar se a dimensao certa reprova. Isso nao passa pelo `RagService`: o `QualitySubject`
e montado direto do JSON. O sistema nao participa -- so o medidor.

**O caso de controle importa tanto quanto os defeituosos.** Um juiz que reprova tudo e tao
inutil quanto um que aprova tudo, e sem uma resposta boa no conjunto os dois pareceriam
iguais.
"""

import json
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

from app.core.exceptions import ValidationError
from app.core.logging import get_logger
from app.quality.base import CitedSource, Expectations, QualityReport, QualitySubject
from app.quality.engine import QualityEngine

logger = get_logger(__name__)


class DetectorCase(BaseModel):
    """Uma resposta com defeito conhecido -- ou, no controle, sem defeito."""

    id: str = Field(min_length=1, max_length=64)
    note: str = Field(min_length=1, max_length=500)
    task: str
    answer: str
    claims: list[str] = Field(default_factory=list)
    sources: list[CitedSource] = Field(default_factory=list)
    expected_topics: list[str] = Field(default_factory=list)

    #: A dimensao que DEVE reprovar. `None` no caso de controle.
    defect: str | None = None
    #: Nota maxima aceitavel para essa dimensao. Nao e um limite de aprovacao do sistema:
    #: e a exigencia de sensibilidade do detector.
    expect_below: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def a_defect_needs_a_bound(self) -> "DetectorCase":
        """Declarar o defeito sem dizer o quanto e demais nao verifica nada."""
        if (self.defect is None) != (self.expect_below is None):
            raise ValueError(f"caso {self.id!r}: `defect` e `expect_below` andam juntos")
        return self

    def subject(self) -> QualitySubject:
        return QualitySubject(
            task=self.task,
            answer=self.answer,
            claims=self.claims or ([self.answer] if self.answer else []),
            sources=self.sources,
            answered=True,
            # Todos os casos simulam resposta de RAG: e o contexto em que `grounding`
            # aplica, e ele e a dimensao mais importante a exercitar.
            source_based=True,
            expectations=(
                Expectations(expected_topics=self.expected_topics) if self.expected_topics else None
            ),
        )


class DetectorResult(BaseModel):
    """O que o detector disse sobre um caso."""

    case_id: str
    note: str
    defect: str | None
    expect_below: float | None
    score: float
    dimension_score: float | None = Field(
        default=None, description="Nota da dimensao que deveria reprovar."
    )
    applicable: bool = True
    detected: bool
    reason: str = ""
    cost_usd: float = 0.0

    @property
    def label(self) -> str:
        return "controle" if self.defect is None else self.defect


def load_detector_cases(path: Path) -> list[DetectorCase]:
    if not path.exists():
        raise ValidationError(f"Conjunto do detector nao encontrado: {path}")

    dados = json.loads(path.read_text(encoding="utf-8"))
    casos = [DetectorCase.model_validate(item) for item in dados]

    if not any(caso.defect is None for caso in casos):
        # Sem controle, um detector que reprova tudo passaria com nota cheia.
        raise ValidationError("O conjunto precisa de ao menos um caso de controle.")
    return casos


async def run_detector(engine: QualityEngine, casos: list[DetectorCase]) -> list[DetectorResult]:
    """Roda o motor sobre cada caso e verifica se a dimensao certa reprovou."""
    resultados = []
    for caso in casos:
        relatorio: QualityReport = await engine.evaluate(caso.subject())

        if caso.defect is None:
            # Controle: espera-se que o agregado NAO reprove.
            detectado = relatorio.passed
            nota_dim, aplicavel, motivo = None, True, "caso de controle"
        else:
            nota = next((n for n in relatorio.dimensions if n.dimension == caso.defect), None)
            aplicavel = bool(nota and nota.applicable)
            nota_dim = nota.score if nota else None
            detectado = bool(nota and nota.applicable and nota.score <= (caso.expect_below or 0.0))
            motivo = nota.reason if nota else "dimensao ausente do relatorio"

        resultados.append(
            DetectorResult(
                case_id=caso.id,
                note=caso.note,
                defect=caso.defect,
                expect_below=caso.expect_below,
                score=relatorio.score,
                dimension_score=nota_dim,
                applicable=aplicavel,
                detected=detectado,
                reason=motivo,
                cost_usd=relatorio.cost_usd,
            )
        )
        logger.info("detector_case_done", case_id=caso.id, defect=caso.defect, detected=detectado)

    return resultados


def render_detector_markdown(resultados: list[DetectorResult]) -> str:
    """Relatorio do detector, com o que o limite pode se apoiar."""
    bons = [r for r in resultados if r.defect is None]
    ruins = [r for r in resultados if r.defect is not None]
    acertos = sum(1 for r in resultados if r.detected)

    linhas = [
        "# Sensibilidade do detector",
        "",
        "Respostas com defeito **conhecido**, escritas a mao. Mede o motor de qualidade,",
        "e nao o sistema: nenhuma passa pelo `RagService`.",
        "",
        f"- **Defeitos detectados:** {sum(1 for r in ruins if r.detected)}/{len(ruins)}",
        f"- **Controles aprovados:** {sum(1 for r in bons if r.detected)}/{len(bons)}",
        f"- **Total:** {acertos}/{len(resultados)}",
        f"- **Custo:** US$ {sum(r.cost_usd for r in resultados):.6f}",
        "",
        "| Caso | Defeito | Nota da dimensao | Agregado | Detectou? |",
        "|---|---|---|---|---|",
    ]
    for r in resultados:
        dim = f"{r.dimension_score:.2f}" if r.dimension_score is not None else "--"
        linhas.append(
            f"| `{r.case_id}` | {r.label} | {dim} | {r.score:.2f} | "
            f"{'sim' if r.detected else '**NAO**'} |"
        )

    if bons and ruins:
        pior_bom = min(r.score for r in bons)
        melhor_ruim = max(r.score for r in ruins)
        linhas += [
            "",
            "## Onde o limite pode ficar",
            "",
            f"- pior resposta **boa**: {pior_bom:.2f}",
            f"- melhor resposta **ruim**: {melhor_ruim:.2f}",
            "",
        ]
        if pior_bom > melhor_ruim:
            meio = (pior_bom + melhor_ruim) / 2
            linhas.append(
                f"As faixas **nao se sobrepoem**. Qualquer limite entre {melhor_ruim:.2f} e "
                f"{pior_bom:.2f} separa as duas; o meio da faixa e {meio:.2f}."
            )
        else:
            linhas.append(
                "As faixas **se sobrepoem**: ha resposta ruim pontuando tao alto quanto uma "
                "boa. Nenhum limite separa as duas, e o agregado sozinho nao serve de "
                "portao -- e o que torna as dimensoes criticas necessarias."
            )

    return "\n".join(linhas) + "\n"
