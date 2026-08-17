"""Construcao do `QualitySubject` a partir do que uma execucao produziu.

Recebe dicionarios simples, e nao `WorkflowState` nem modelos do SQLAlchemy. A razao e a
mesma que fez o motor existir: se este modulo importasse o grafo, o pacote `quality`
passaria a depender do LangGraph -- e o modo offline, que le JSON de um arquivo, teria de
carregar meio framework para avaliar uma linha de dataset.

O preco dessa escolha e que quem chama precisa desmontar o estado antes. Sao dez linhas no
no do grafo, e valem o isolamento.
"""

from collections.abc import Sequence
from typing import Any

from app.quality.base import CitedSource, Expectations, QualitySubject

#: Teto de afirmacoes extraidas do relatorio. Alinhado com `MAX_CLAIMS` do grounding: nao
#: adianta extrair cinquenta se so vinte serao verificadas.
MAX_CLAIMS = 20


def subject_from_report(
    *,
    task: str,
    report: dict[str, Any] | None,
    research: dict[str, Any] | None = None,
    steps: Sequence[Any] = (),
    expectations: Expectations | None = None,
) -> QualitySubject:
    """Monta o subject de uma execucao do workflow.

    Args:
        report: a saida do agente de relatorio. `None` quando ele falhou -- e ai o que
            sobra ainda merece ser avaliado, porque `api_reliability` tem o que dizer.
        research: a saida do no de pesquisa, de onde vem `answered` e as fontes citadas.
        steps: passos gravados, ja convertidos em `StepFacts` por quem chama.
    """
    resumo = str((report or {}).get("executive_summary", "")).strip()
    pontos = [
        str(item).strip() for item in (report or {}).get("key_points", []) if str(item).strip()
    ]

    fontes = _sources_from(research)
    # `source_based` distingue "analise de dados fornecidos" de "resposta de RAG": o no de
    # pesquisa ter rodado e o sinal de que a resposta deveria se apoiar na base.
    baseada_em_fontes = research is not None
    respondeu = bool((research or {}).get("answered", True))

    return QualitySubject(
        task=task,
        answer=resumo,
        # Os pontos-chave sao as afirmacoes verificaveis; o resumo entra como uma a mais
        # quando nao ha pontos, para que grounding tenha o que checar.
        claims=(pontos or ([resumo] if resumo else []))[:MAX_CLAIMS],
        sources=fontes,
        answered=respondeu,
        source_based=baseada_em_fontes,
        steps=list(steps),
        expectations=expectations,
    )


def _sources_from(research: dict[str, Any] | None) -> list[CitedSource]:
    """Extrai as fontes citadas pela pesquisa.

    Le apenas `sources`, que o no de pesquisa preenche **somente com os trechos de fato
    citados** pela resposta (ver `WorkflowNodes.research`). Usar os trechos recuperados
    seria mais generoso e erraria o alvo: a pergunta do grounding e se a afirmacao tem
    fonte entre as que a resposta ALEGA ter usado.
    """
    if not research:
        return []

    fontes = []
    for item in research.get("sources", []) or []:
        if not isinstance(item, dict):
            continue
        fontes.append(
            CitedSource(
                document_id=str(item.get("document_id", "")),
                filename=item.get("filename"),
                excerpt=str(item.get("excerpt", "")),
                score=item.get("score"),
            )
        )
    return fontes
