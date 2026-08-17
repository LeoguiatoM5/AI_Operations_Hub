"""Resultado de uma rodada de avaliacao, em JSON e em markdown.

O relatorio existe para ser **comparado com o anterior**. Todo campo de cabecalho serve a
isso: provider, modelo, modelo de embedding e limite de relevancia. Duas rodadas com
configuracoes diferentes produzem numeros que nao se comparam, e sem o cabecalho ninguem
percebe -- so ve uma nota que subiu.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.db.types import utcnow
from app.evals.assertions import AssertionResult
from app.quality.base import QualityReport


class CaseResult(BaseModel):
    """O que aconteceu com um caso do conjunto."""

    case_id: str
    question: str
    note: str = ""
    answered: bool = False
    answer: str = ""
    cited_sources: list[str] = Field(default_factory=list)
    assertions: list[AssertionResult] = Field(default_factory=list)
    quality: QualityReport | None = None
    best_score: float = 0.0
    cost_usd: float = 0.0
    error: str | None = Field(default=None, description="Preenchido quando o caso quebrou.")

    @property
    def assertions_passed(self) -> bool:
        return all(item.passed for item in self.assertions)

    @property
    def failed_assertions(self) -> list[AssertionResult]:
        return [item for item in self.assertions if not item.passed]


class EvalReport(BaseModel):
    """Uma rodada completa."""

    generated_at: datetime = Field(default_factory=utcnow)
    llm_provider: str = ""
    llm_model: str = ""
    embedding_provider: str = ""
    embedding_model: str = ""
    min_relevant_score: float = 0.0
    quality_threshold: float = 0.0
    judged: bool = Field(
        default=False, description="Se as dimensoes por LLM rodaram, alem das deterministicas."
    )
    cases: list[CaseResult] = Field(default_factory=list)

    # ------------------------------------------------------------------ agregados

    @property
    def total(self) -> int:
        return len(self.cases)

    @property
    def passed(self) -> list[CaseResult]:
        """Casos em que TODAS as assercoes deterministicas passaram.

        O veredito do relatorio se apoia nas assercoes, e nao nas notas: elas nao tem
        vies, nao custam e nao mudam entre execucoes. As notas descrevem *quao bem*; as
        assercoes dizem *se*.
        """
        return [caso for caso in self.cases if caso.assertions_passed and not caso.error]

    @property
    def pass_rate(self) -> float:
        return round(len(self.passed) / self.total, 4) if self.total else 0.0

    @property
    def cost_usd(self) -> float:
        return round(sum(caso.cost_usd for caso in self.cases), 6)

    def dimension_averages(self) -> dict[str, float]:
        """Media por dimensao, considerando apenas onde ela foi aplicavel.

        E o numero que vai calibrar os pesos e o limite: uma dimensao que da 0.95 em todo
        o conjunto nao esta separando nada e nao merece peso alto.
        """
        somas: dict[str, list[float]] = {}
        for caso in self.cases:
            if caso.quality is None:
                continue
            for nota in caso.quality.applicable:
                somas.setdefault(nota.dimension, []).append(nota.score)
        return {
            nome: round(sum(valores) / len(valores), 4) for nome, valores in sorted(somas.items())
        }

    def assertion_failures(self) -> dict[str, int]:
        """Quantas vezes cada assercao falhou, da mais frequente para a menos."""
        contagem: dict[str, int] = {}
        for caso in self.cases:
            for item in caso.failed_assertions:
                contagem[item.name] = contagem.get(item.name, 0) + 1
        return dict(sorted(contagem.items(), key=lambda par: -par[1]))

    def to_json(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["summary"] = {
            "total": self.total,
            "passed": len(self.passed),
            "pass_rate": self.pass_rate,
            "cost_usd": self.cost_usd,
            "dimension_averages": self.dimension_averages(),
            "assertion_failures": self.assertion_failures(),
        }
        return payload


def render_markdown(report: EvalReport) -> str:
    """Relatorio legivel, para commitar junto do JSON.

    Dois formatos de proposito: o JSON e para diferenciar duas rodadas com ferramenta; o
    markdown e para alguem ler em trinta segundos e saber se piorou.
    """
    linhas = [
        "# Relatorio de avaliacao",
        "",
        f"**Gerado em:** {report.generated_at:%Y-%m-%d %H:%M:%S} UTC",
        "",
        "| Configuracao | Valor |",
        "|---|---|",
        f"| LLM | `{report.llm_provider}` / `{report.llm_model}` |",
        f"| Embeddings | `{report.embedding_provider}` / `{report.embedding_model}` |",
        f"| Corte de relevancia | {report.min_relevant_score} |",
        f"| Dimensoes por LLM | {'sim' if report.judged else 'nao (apenas assercoes)'} |",
        "",
    ]

    if report.embedding_provider == "fake":
        linhas += [
            "> **Atencao.** Esta rodada usou embeddings falsos, que comparam palavras e nao",
            "> significado. Os numeros medem o encanamento -- que o fluxo roda, que as",
            "> assercoes sao avaliadas -- e **nao** a qualidade da recuperacao. Para medir",
            "> qualidade, rode com `EMBEDDING_PROVIDER=openai`.",
            "",
        ]

    linhas += [
        "## Resumo",
        "",
        f"- **Casos:** {report.total}",
        f"- **Assercoes aprovadas:** {len(report.passed)}/{report.total} ({report.pass_rate:.0%})",
        f"- **Custo da rodada:** US$ {report.cost_usd:.6f}",
        "",
    ]

    medias = report.dimension_averages()
    if medias:
        linhas += ["### Media por dimensao", "", "| Dimensao | Media |", "|---|---|"]
        linhas += [f"| `{nome}` | {valor:.3f} |" for nome, valor in medias.items()]
        linhas.append("")

    falhas = report.assertion_failures()
    if falhas:
        linhas += ["### Assercoes que falharam", "", "| Assercao | Casos |", "|---|---|"]
        linhas += [f"| `{nome}` | {qtd} |" for nome, qtd in falhas.items()]
        linhas.append("")

    linhas += ["## Casos", "", "| Caso | Assercoes | Nota | Observacao |", "|---|---|---|---|"]
    for caso in report.cases:
        simbolo = "erro" if caso.error else ("ok" if caso.assertions_passed else "FALHOU")
        nota = f"{caso.quality.score:.2f}" if caso.quality else "--"
        detalhe = caso.error or "; ".join(item.detail for item in caso.failed_assertions) or ""
        linhas.append(f"| `{caso.case_id}` | {simbolo} | {nota} | {detalhe[:160]} |")

    problemas = [caso for caso in report.cases if not caso.assertions_passed or caso.error]
    if problemas:
        linhas += ["", "## Detalhe dos casos com problema", ""]
        for caso in problemas:
            linhas += [
                f"### `{caso.case_id}`",
                "",
                f"**Pergunta:** {caso.question}",
                "",
                f"**Por que este caso existe:** {caso.note}",
                "",
            ]
            if caso.error:
                linhas += [f"**Erro:** {caso.error}", ""]
            for item in caso.failed_assertions:
                linhas.append(f"- `{item.name}`: {item.detail}")
            if caso.quality and caso.quality.failures:
                linhas += ["", "Dimensoes abaixo do limite:", ""]
                linhas += [
                    f"- `{nota.dimension}` ({nota.score:.2f}): {nota.reason}"
                    for nota in caso.quality.failures
                ]
            linhas.append("")

    return "\n".join(linhas) + "\n"
