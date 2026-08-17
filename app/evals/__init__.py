"""Avaliacao offline: o mesmo motor de qualidade sobre um conjunto versionado.

A diferenca para o modo online nao esta no motor -- e o mesmo `QualityEngine`. Esta no
que o conjunto acrescenta: **expectativas escritas por uma pessoa**.

Isso muda a natureza da medicao. Online, o juiz decide sozinho o que seria uma boa
resposta, e a nota carrega o vies de o avaliador ser o proprio avaliado. Aqui a lista do
que se espera e verdade externa ao modelo -- e parte das verificacoes nem precisa de LLM:
saber se o sistema respondeu quando nao deveria e comparacao de booleano.
"""

from app.evals.dataset import EvalCase, load_dataset
from app.evals.report import CaseResult, EvalReport, render_markdown
from app.evals.runner import EvalRunner

__all__ = [
    "CaseResult",
    "EvalCase",
    "EvalReport",
    "EvalRunner",
    "load_dataset",
    "render_markdown",
]
