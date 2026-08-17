"""Execucao do conjunto de avaliacao.

Roda o **sistema de verdade** sobre cada pergunta -- o mesmo `RagService` que atende
`/rag/query` -- e so entao avalia o resultado. A alternativa seria avaliar respostas
gravadas de antemao, o que mediria apenas o juiz e continuaria verde enquanto o sistema
apodrecia.

Um caso que quebra nao derruba a rodada: o erro entra no relatorio e os demais seguem.
Uma suite de avaliacao que aborta na primeira falha nao serve para medir um sistema que
esta justamente sendo investigado por falhar.
"""

import asyncio

from app.core.exceptions import AIHubError
from app.core.logging import get_logger
from app.evals.assertions import run_assertions
from app.evals.dataset import EvalCase
from app.evals.report import CaseResult
from app.quality.base import CitedSource, QualitySubject
from app.quality.engine import QualityEngine
from app.services.rag_service import RagResult, RagService

logger = get_logger(__name__)


class EvalRunner:
    """Roda os casos e produz um resultado por caso."""

    def __init__(
        self,
        rag: RagService,
        quality: QualityEngine | None = None,
        *,
        concurrency: int = 3,
    ) -> None:
        """
        Args:
            quality: sem ele, so as assercoes deterministicas rodam -- rapido, gratuito e
                suficiente para a CI, que nao pode depender de segredo de LLM.
            concurrency: casos em paralelo. Baixo de proposito: um conjunto de vinte
                perguntas disparado de uma vez costuma bater no limite de requisicoes do
                provedor, e um 429 no meio da avaliacao contamina a medicao.
        """
        self._rag = rag
        self._quality = quality
        self._semaphore = asyncio.Semaphore(concurrency)

    async def run(self, cases: list[EvalCase]) -> list[CaseResult]:
        """Roda todos os casos, preservando a ordem do conjunto."""
        return list(await asyncio.gather(*(self._run_one(caso) for caso in cases)))

    async def _run_one(self, case: EvalCase) -> CaseResult:
        async with self._semaphore:
            try:
                resultado = await self._rag.query(case.question)
            except AIHubError as error:
                logger.warning("eval_case_failed", case_id=case.id, error_code=error.code)
                return CaseResult(
                    case_id=case.id,
                    question=case.question,
                    note=case.note,
                    error=f"{error.code}: {error.message}",
                )

            return await self._evaluate(case, resultado)

    async def _evaluate(self, case: EvalCase, resultado: RagResult) -> CaseResult:
        texto = resultado.answer.answer if resultado.answer else ""
        citadas = [
            str(hit.metadata.get("filename", hit.document_id)) for hit in resultado.cited_sources
        ]

        assercoes = run_assertions(case, answered=resultado.answered, answer=texto, cited=citadas)

        relatorio = None
        custo = resultado.cost_usd
        if self._quality is not None:
            subject = QualitySubject(
                task=case.question,
                answer=texto,
                claims=[texto] if texto else [],
                sources=[
                    CitedSource(
                        document_id=hit.document_id,
                        filename=str(hit.metadata.get("filename", "")) or None,
                        excerpt=hit.text,
                        score=hit.score,
                    )
                    for hit in resultado.cited_sources
                ],
                answered=resultado.answered,
                # Sempre verdadeiro aqui: todo caso do conjunto e uma consulta a base.
                source_based=True,
                expectations=case.expectations(),
            )
            relatorio = await self._quality.evaluate(subject)
            custo = round(custo + relatorio.cost_usd, 8)

        logger.info(
            "eval_case_done",
            case_id=case.id,
            answered=resultado.answered,
            assertions_passed=all(item.passed for item in assercoes),
            score=relatorio.score if relatorio else None,
        )

        return CaseResult(
            case_id=case.id,
            question=case.question,
            note=case.note,
            answered=resultado.answered,
            answer=texto,
            cited_sources=citadas,
            assertions=assercoes,
            quality=relatorio,
            best_score=round(resultado.best_score, 4),
            cost_usd=custo,
        )
