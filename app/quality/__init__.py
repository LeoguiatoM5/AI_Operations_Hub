"""Motor de qualidade: mede o que o sistema produziu antes de entregar.

**Um motor, dois modos.** O mesmo codigo roda antes de responder a um usuario (online) e
sobre um conjunto de avaliacao versionado (offline). Se fossem dois sistemas, divergiriam
em uma semana -- e o relatorio de evals passaria a medir algo que a producao nao faz.

O que torna isso possivel e o `QualitySubject`: uma descricao do que foi produzido, sem
nenhuma dependencia de FastAPI, de banco ou do LangGraph. Uma execucao real vira um
`QualitySubject`; uma entrada do dataset tambem.
"""

from app.quality.base import (
    CitedSource,
    Dimension,
    DimensionScore,
    Expectations,
    QualityReport,
    QualitySubject,
    StepFacts,
)
from app.quality.completeness import CompletenessDimension
from app.quality.consistency import ConsistencyDimension
from app.quality.engine import DEFAULT_WEIGHTS, QualityEngine
from app.quality.factory import build_quality_engine
from app.quality.grounding import GroundingDimension
from app.quality.relevance import RelevanceDimension
from app.quality.reliability import ApiReliabilityDimension

__all__ = [
    "DEFAULT_WEIGHTS",
    "ApiReliabilityDimension",
    "CitedSource",
    "CompletenessDimension",
    "ConsistencyDimension",
    "Dimension",
    "DimensionScore",
    "Expectations",
    "GroundingDimension",
    "QualityEngine",
    "QualityReport",
    "QualitySubject",
    "RelevanceDimension",
    "StepFacts",
    "build_quality_engine",
]
