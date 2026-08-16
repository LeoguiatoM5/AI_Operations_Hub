"""Testes da configuracao de RAG.

Configuracao invalida precisa derrubar a aplicacao no startup. A alternativa e descobrir
o problema na primeira indexacao, com uma mensagem vinda das entranhas de uma biblioteca.
"""

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def make_settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


def test_min_score_defaults_to_the_provider_floor() -> None:
    """`None` significa "use o piso do provedor", que conhece a propria escala."""
    assert make_settings().rag_min_score is None


def test_explicit_min_score_is_accepted() -> None:
    assert make_settings(rag_min_score=0.4).rag_min_score == 0.4


@pytest.mark.parametrize("valor", [-0.1, 1.5])
def test_min_score_outside_the_range_is_rejected(valor: float) -> None:
    with pytest.raises(ValidationError):
        make_settings(rag_min_score=valor)


@pytest.mark.parametrize("nome", ["ab", "-comeca-com-hifen", "termina-com-hifen-", "com espaco"])
def test_invalid_collection_name_is_rejected_on_startup(nome: str) -> None:
    """O Chroma exige 3-512 caracteres de [a-zA-Z0-9._-], comecando e terminando em
    alfanumerico. Sem esta validacao, o erro so apareceria ao indexar o primeiro
    documento."""
    with pytest.raises(ValidationError):
        make_settings(chroma_collection=nome)


@pytest.mark.parametrize("nome", ["abc", "knowledge_base", "base-2026", "a.b.c"])
def test_valid_collection_names_are_accepted(nome: str) -> None:
    assert make_settings(chroma_collection=nome).chroma_collection == nome
