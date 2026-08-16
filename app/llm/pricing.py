"""Estimativa de custo por chamada.

Custo e um requisito de produto, nao um detalhe: um agente que consulta a base, analisa
dados e gera relatorio pode disparar dezenas de chamadas por execucao. Sem medir, nao ha
como responder "quanto custa rodar isso mil vezes por mes".

A tabela e uma aproximacao mantida manualmente -- precos de API mudam. O valor gravado
em cada execucao e sempre uma ESTIMATIVA; a fonte de verdade e a fatura do provedor.
"""

from dataclasses import dataclass

from app.core.logging import get_logger
from app.llm.base import TokenUsage

logger = get_logger(__name__)

#: Data da ultima conferencia manual da tabela de precos.
PRICING_REVIEWED_ON = "2026-08"


@dataclass(frozen=True)
class ModelPricing:
    """Preco em dolares por milhao de tokens."""

    input_per_million: float
    output_per_million: float


#: Precos por modelo. Conferir na pagina oficial do provedor ao atualizar.
PRICING: dict[str, ModelPricing] = {
    # OpenAI
    "gpt-4o-mini": ModelPricing(input_per_million=0.15, output_per_million=0.60),
    "gpt-4o": ModelPricing(input_per_million=2.50, output_per_million=10.00),
    "gpt-4.1-mini": ModelPricing(input_per_million=0.40, output_per_million=1.60),
    "gpt-4.1": ModelPricing(input_per_million=2.00, output_per_million=8.00),
    # Embeddings: so ha custo de entrada. O vetor devolvido nao e cobrado como saida.
    "text-embedding-3-small": ModelPricing(input_per_million=0.02, output_per_million=0.0),
    "text-embedding-3-large": ModelPricing(input_per_million=0.13, output_per_million=0.0),
    # providers de teste: nao custam nada
    "fake-model-1": ModelPricing(input_per_million=0.0, output_per_million=0.0),
    "fake-embedding-1": ModelPricing(input_per_million=0.0, output_per_million=0.0),
}


def resolve_pricing(model: str) -> ModelPricing | None:
    """Encontra o preco de um modelo, tolerando versoes fixadas.

    O provedor responde com a versao exata que atendeu a chamada -- pedimos
    `gpt-4o-mini` e recebemos `gpt-4o-mini-2024-07-18`. Indexar apenas pelo alias faria
    toda medicao de custo retornar zero silenciosamente.

    A busca tenta a chave exata e, na ausencia dela, o prefixo conhecido mais LONGO.
    O comprimento importa: `gpt-4o-mini-2024-07-18` comeca tanto com `gpt-4o` quanto
    com `gpt-4o-mini`, e cobrar pelo primeiro superestimaria o custo em ate 16 vezes.
    """
    exact = PRICING.get(model)
    if exact is not None:
        return exact

    candidates = [key for key in PRICING if model.startswith(key)]
    if not candidates:
        return None
    return PRICING[max(candidates, key=len)]


def estimate_cost_usd(model: str, usage: TokenUsage) -> float:
    """Calcula o custo estimado de uma chamada.

    Modelo desconhecido nao e erro: registra aviso e devolve 0.0, para que uma tabela
    desatualizada nunca derrube uma execucao em andamento.
    """
    pricing = resolve_pricing(model)
    if pricing is None:
        logger.warning("pricing_unknown_model", model=model)
        return 0.0

    cost = (
        usage.prompt_tokens * pricing.input_per_million
        + usage.completion_tokens * pricing.output_per_million
    ) / 1_000_000
    return round(cost, 8)
