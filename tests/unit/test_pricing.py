"""Testes da estimativa de custo."""

import pytest

from app.llm.base import TokenUsage
from app.llm.pricing import PRICING, estimate_cost_usd, resolve_pricing


def test_cost_uses_separate_input_and_output_rates() -> None:
    """Token de saida custa mais caro que token de entrada -- o calculo precisa separar."""
    usage = TokenUsage(prompt_tokens=1_000_000, completion_tokens=1_000_000)

    cost = estimate_cost_usd("gpt-4o-mini", usage)

    assert cost == 0.75  # 0.15 (entrada) + 0.60 (saida)


def test_cost_scales_with_usage() -> None:
    small = estimate_cost_usd("gpt-4o", TokenUsage(prompt_tokens=1_000))
    large = estimate_cost_usd("gpt-4o", TokenUsage(prompt_tokens=10_000))

    assert large > small > 0


@pytest.mark.parametrize(
    ("returned_by_provider", "expected_alias"),
    [
        ("gpt-4o-mini-2024-07-18", "gpt-4o-mini"),
        ("gpt-4o-2024-08-06", "gpt-4o"),
        ("gpt-4.1-mini-2025-04-14", "gpt-4.1-mini"),
    ],
)
def test_pinned_model_versions_resolve_to_their_alias(
    returned_by_provider: str, expected_alias: str
) -> None:
    """A API responde com a versao fixada, nao com o alias que pedimos.

    Bug real observado em execucao: `gpt-4o-mini-2024-07-18` nao batia com nenhuma
    chave e todo custo saia zerado.
    """
    assert resolve_pricing(returned_by_provider) == PRICING[expected_alias]


def test_prefix_match_prefers_the_most_specific_alias() -> None:
    """`gpt-4o-mini-...` comeca com `gpt-4o` e com `gpt-4o-mini`: vence o mais longo."""
    usage = TokenUsage(prompt_tokens=1_000_000)

    mini_cost = estimate_cost_usd("gpt-4o-mini-2024-07-18", usage)

    assert mini_cost == 0.15  # tarifa do mini, nao os 2.50 do gpt-4o


def test_unknown_model_does_not_break_execution() -> None:
    """Tabela desatualizada nao pode derrubar uma execucao: registra aviso e segue."""
    cost = estimate_cost_usd("modelo-que-nao-existe", TokenUsage(prompt_tokens=5_000))

    assert cost == 0.0


def test_zero_usage_costs_nothing() -> None:
    assert estimate_cost_usd("gpt-4o", TokenUsage()) == 0.0
