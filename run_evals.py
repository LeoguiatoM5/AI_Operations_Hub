"""Roda o conjunto de avaliacao e grava o relatorio.

    python run_evals.py                    # apenas assercoes deterministicas
    python run_evals.py --judge            # + as quatro dimensoes por LLM
    python run_evals.py --case reembolso-prazo --case senha-sms

Sem `--judge` a rodada e gratuita e nao chama LLM para avaliar -- so para responder. E o
modo da CI, que nao pode depender de segredo, e o modo de quem quer saber se o sistema
continua recusando o que deve recusar.

**A base e reconstruida do zero a cada rodada**, num diretorio temporario, a partir de
`evals/corpus/`. Reaproveitar a base de desenvolvimento tornaria o resultado dependente do
que alguem subiu ontem -- e duas rodadas deixariam de ser comparaveis sem que ninguem
percebesse.
"""

import argparse
import asyncio
import json
import shutil
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  -- registra as tabelas em Base.metadata
from app.agents.research import ResearchAgent
from app.core.config import Settings, get_settings
from app.core.exceptions import AIHubError
from app.core.logging import configure_logging
from app.db.base import Base
from app.db.session import create_session_factory
from app.evals.dataset import load_dataset
from app.evals.detector import load_detector_cases, render_detector_markdown, run_detector
from app.evals.report import EvalReport, render_markdown
from app.evals.runner import EvalRunner
from app.llm.factory import build_llm_provider
from app.quality.factory import build_quality_engine
from app.rag.factory import build_embedding_provider
from app.rag.memory_store import InMemoryVectorStore
from app.rag.retriever import Retriever
from app.repositories.document_repository import DocumentRepository
from app.services.document_service import DocumentService
from app.services.rag_service import RagService

RAIZ = Path(__file__).parent
DATASET = RAIZ / "evals" / "evaluation_dataset.json"
DETECTOR_DATASET = RAIZ / "evals" / "detector_cases.json"
CORPUS = RAIZ / "evals" / "corpus"
REPORTS = RAIZ / "evals" / "reports"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Roda o conjunto de avaliacao.")
    parser.add_argument(
        "--judge",
        action="store_true",
        help="Avalia tambem as dimensoes por LLM (custa tokens).",
    )
    parser.add_argument(
        "--case",
        action="append",
        default=[],
        metavar="ID",
        help="Roda apenas os casos indicados. Pode repetir.",
    )
    parser.add_argument(
        "--detector",
        action="store_true",
        help="Roda o conjunto de respostas com defeito conhecido: mede o MOTOR DE "
        "QUALIDADE, e nao o sistema. Nao consulta a base. Exige LLM.",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="So verifica que o fluxo roda: aprova se nenhum caso quebrou, ignorando as "
        "assercoes semanticas. E o modo da CI, com provider deterministico.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=REPORTS,
        help="Diretorio de saida dos relatorios.",
    )
    return parser.parse_args()


async def ingest_corpus(service: DocumentService) -> int:
    """Sobe o corpus na base limpa."""
    arquivos = sorted(CORPUS.glob("*.md"))
    if not arquivos:
        raise AIHubError(f"Corpus vazio: {CORPUS}")

    for arquivo in arquivos:
        await service.ingest(arquivo.read_bytes(), filename=arquivo.name)
    return len(arquivos)


async def run_detector_mode(settings: Settings, saida: Path) -> int:
    """Mede a sensibilidade do motor de qualidade, sem tocar no sistema.

    Nao ha ingestao, nao ha recuperacao, nao ha `RagService`: os subjects vem prontos do
    JSON. E o unico modo que consegue responder "que nota uma resposta ruim tira?" -- e
    sem essa resposta o limite de aprovacao e chute.
    """
    provider = build_llm_provider(settings)
    try:
        casos = load_detector_cases(DETECTOR_DATASET)
        motor = build_quality_engine(provider, threshold=settings.quality_threshold)

        print(f"Casos: {len(casos)} | LLM: {provider.name}/{provider.model}\n")
        resultados = await run_detector(motor, casos)
    finally:
        await provider.aclose()

    for item in resultados:
        marca = "ok   " if item.detected else "FALHA"
        dim = f"{item.dimension_score:.2f}" if item.dimension_score is not None else "  --"
        print(f"  [{marca}] {dim}  {item.case_id:<34} ({item.label})")

    # Em thread separada: escrever arquivo bloqueia o event loop, e a regra `ASYNC240`
    # existe justamente para nao deixar isso passar por descuido. Aqui o custo seria
    # invisivel -- a rodada ja terminou --, mas a excecao viraria habito.
    destino = saida / "detector.md"
    await asyncio.to_thread(saida.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(
        destino.write_text, render_detector_markdown(resultados), encoding="utf-8"
    )

    acertos = sum(1 for item in resultados if item.detected)
    print(f"\n{acertos}/{len(resultados)} corretos | relatorio: {destino}")
    return 0 if acertos == len(resultados) else 1


async def main() -> int:
    args = parse_args()
    settings: Settings = get_settings()
    configure_logging(settings.model_copy(update={"log_level": "WARNING"}))

    if args.detector:
        return await run_detector_mode(settings, args.out)

    casos = load_dataset(DATASET)
    if args.case:
        pedidos = set(args.case)
        desconhecidos = pedidos - {caso.id for caso in casos}
        if desconhecidos:
            print(f"Casos inexistentes: {sorted(desconhecidos)}", file=sys.stderr)
            return 2
        casos = [caso for caso in casos if caso.id in pedidos]

    temporario = Path(tempfile.mkdtemp(prefix="aiops-evals-"))
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    provider = build_llm_provider(settings)
    embedder = build_embedding_provider(settings)
    # Indice em memoria: a rodada nao deve tocar a base de desenvolvimento nem deixar
    # residuo em `data/chroma`.
    store = InMemoryVectorStore()

    try:
        async with engine.begin() as conexao:
            await conexao.run_sync(Base.metadata.create_all)

        factory = create_session_factory(engine)
        async with factory() as session:
            documentos = DocumentRepository(session)
            total_docs = await ingest_corpus(
                DocumentService(
                    documentos,
                    embedder,
                    store,
                    chunk_size=settings.chunk_size,
                    chunk_overlap=settings.chunk_overlap,
                    max_size_bytes=settings.max_upload_bytes,
                )
            )
            await session.commit()

            retriever = Retriever(
                embedder, store, top_k=settings.rag_top_k, min_score=settings.rag_min_score
            )
            rag = RagService(
                retriever,
                ResearchAgent(provider),
                documentos,
                embedding_model=embedder.model,
            )
            motor = build_quality_engine(
                provider if args.judge else None, threshold=settings.quality_threshold
            )

            print(f"Corpus: {total_docs} documento(s) | Casos: {len(casos)}")
            print(f"LLM: {provider.name}/{provider.model} | Embeddings: {embedder.name}")
            print(f"Dimensoes por LLM: {'sim' if args.judge else 'nao'}\n")

            resultados = await EvalRunner(rag, motor if args.judge else None).run(casos)

        relatorio = EvalReport(
            llm_provider=provider.name,
            llm_model=provider.model,
            embedding_provider=embedder.name,
            embedding_model=embedder.model,
            min_relevant_score=retriever.min_score,
            quality_threshold=settings.quality_threshold,
            judged=args.judge,
            cases=resultados,
        )
    finally:
        await provider.aclose()
        await embedder.aclose()
        await store.aclose()
        await engine.dispose()
        shutil.rmtree(temporario, ignore_errors=True)

    args.out.mkdir(parents=True, exist_ok=True)
    carimbo = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destino_json = args.out / f"eval-{carimbo}.json"
    destino_md = args.out / f"eval-{carimbo}.md"
    destino_json.write_text(
        json.dumps(relatorio.to_json(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    destino_md.write_text(render_markdown(relatorio), encoding="utf-8")

    for caso in relatorio.cases:
        marca = "erro " if caso.error else ("ok   " if caso.assertions_passed else "FALHA")
        nota = f"{caso.quality.score:.2f}" if caso.quality else "  --"
        print(f"  [{marca}] {nota}  {caso.case_id}")

    print(
        f"\n{len(relatorio.passed)}/{relatorio.total} casos aprovados "
        f"({relatorio.pass_rate:.0%}) | custo US$ {relatorio.cost_usd:.6f}"
    )
    print(f"Relatorio: {destino_md}")

    if args.smoke:
        # **Por que a CI nao pode exigir as assercoes semanticas.** Com provider
        # deterministico, o sistema nao entende as perguntas: ele nao tem como saber
        # quando recusar nem o que citar. Cobrar isso de um substituto seria medir
        # compreensao num boneco -- o resultado seria sempre reprovado, e o passo viraria
        # ruido que todo mundo aprende a ignorar.
        #
        # O que este modo verifica e real e vale o passo: que os 16 casos atravessam
        # ingestao, recuperacao, chamada estruturada e avaliacao **sem quebrar**. Uma
        # regressao de encanamento -- schema incompativel, contrato mudado, excecao nova --
        # aparece aqui. A medicao de qualidade exige modelo de verdade, e roda a mao.
        quebrados = [caso for caso in relatorio.cases if caso.error]
        for caso in quebrados:
            print(f"  QUEBROU {caso.case_id}: {caso.error}")
        print(f"\nmodo smoke: {relatorio.total - len(quebrados)}/{relatorio.total} sem erro")
        return 1 if quebrados else 0

    # Codigo de saida diferente de zero quando ha reprovacao: e o que permite quebrar um
    # build por queda de qualidade, e nao apenas registrar o numero.
    return 0 if len(relatorio.passed) == relatorio.total else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
