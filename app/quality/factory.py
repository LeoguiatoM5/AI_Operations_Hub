"""Montagem do motor de qualidade.

Um lugar so, pelo mesmo motivo do registro de ferramentas: se cada consumidor montasse o
seu, o modo online e o modo offline mediriam coisas diferentes -- e o relatorio de evals
deixaria de descrever a producao, que e exatamente o que o V5 existe para evitar.
"""

from app.core.logging import get_logger
from app.llm.base import LLMProvider
from app.quality.completeness import CompletenessDimension
from app.quality.consistency import ConsistencyDimension
from app.quality.engine import QualityEngine
from app.quality.grounding import GroundingDimension
from app.quality.relevance import RelevanceDimension
from app.quality.reliability import ApiReliabilityDimension

logger = get_logger(__name__)


def build_quality_engine(
    provider: LLMProvider | None = None,
    *,
    threshold: float = 0.7,
) -> QualityEngine:
    """Monta o motor com as dimensoes disponiveis.

    Args:
        provider: sem ele, o motor roda apenas `api_reliability` -- gratuito e sem rede.
            Nao e um modo degradado por acidente: e o motor que a CI usa e o que permite
            medir confiabilidade sobre um historico inteiro de execucoes sem pagar nada.
    """
    if provider is None:
        engine = QualityEngine([ApiReliabilityDimension()], threshold=threshold)
        logger.info("quality_engine_built", dimensions=engine.dimension_names, uses_llm=False)
        return engine

    engine = QualityEngine(
        [
            GroundingDimension(provider),
            RelevanceDimension(provider),
            CompletenessDimension(provider),
            ConsistencyDimension(provider),
            ApiReliabilityDimension(),
        ],
        threshold=threshold,
    )
    logger.info("quality_engine_built", dimensions=engine.dimension_names, uses_llm=True)
    return engine
