"""Testes das migracoes.

**O teste que justifica este arquivo e `test_the_models_match_the_migrations`.**

A suite cria as tabelas com `Base.metadata.create_all` -- rapido, e o certo para banco em
memoria. Producao aplica migracoes. Sao dois caminhos para o mesmo esquema, e caminhos
paralelos divergem: alguem acrescenta uma coluna no modelo, os testes passam (porque
`create_all` a cria), e a migracao correspondente nunca e escrita. O desvio so aparece no
deploy, quando a coluna nao existe no banco de producao.

`alembic check` compara os modelos com o resultado das migracoes e falha se houver
diferenca. E o unico jeito de manter os dois caminhos honestos.
"""

import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).parents[2]


def alembic(*argumentos: str, url: str) -> subprocess.CompletedProcess[str]:
    """Roda o Alembic como subprocesso.

    Como subprocesso, e nao pela API Python, de proposito: e assim que ele roda em
    producao e na CI. Chamar a API interna testaria um caminho que ninguem usa.
    """
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-x", f"url={url}", *argumentos],
        cwd=RAIZ,
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.fixture
def url(temp_dir: Path) -> str:
    return f"sqlite+aiosqlite:///{(temp_dir / 'migracao.db').as_posix()}"


def test_the_models_match_the_migrations(url: str) -> None:
    """Nenhum modelo mudou sem a migracao correspondente.

    Falhou? Gere a migracao que falta:

        alembic revision --autogenerate -m "descreva a mudanca"

    E confira o arquivo gerado antes de commitar -- o autogenerate acerta a maior parte, e
    erra justamente onde a mudanca e interessante (renomear coluna vira remover e criar,
    perdendo os dados).
    """
    assert alembic("upgrade", "head", url=url).returncode == 0

    resultado = alembic("check", url=url)

    assert resultado.returncode == 0, (
        "Os modelos divergiram das migracoes. Rode "
        f"`alembic revision --autogenerate`.\n{resultado.stdout}\n{resultado.stderr}"
    )


def test_the_migration_knows_how_to_go_back(url: str) -> None:
    """Uma migracao sem `downgrade` funcional e uma via de mao unica.

    Nao e sobre reverter em producao -- e sobre poder testar a ida e a volta num banco
    descartavel antes de aplicar em qualquer lugar.
    """
    assert alembic("upgrade", "head", url=url).returncode == 0

    resultado = alembic("downgrade", "base", url=url)

    assert resultado.returncode == 0, resultado.stderr


def test_migrations_do_not_depend_on_application_code() -> None:
    """Uma migracao de dois anos atras precisa continuar rodando.

    Se ela importar `app.db.types.StrEnumType`, passa a depender de uma classe que pode
    ter mudado de nome ou deixado de existir. Migracao descreve o BANCO -- e o banco nao
    sabe o que e um `StrEnum`. O `render_item` em `migrations/env.py` desembrulha os
    `TypeDecorator` para o tipo do banco justamente por isso.
    """
    versoes = list((RAIZ / "migrations" / "versions").glob("*.py"))
    assert versoes, "nenhuma migracao encontrada"

    for arquivo in versoes:
        conteudo = arquivo.read_text(encoding="utf-8")
        assert "app." not in conteudo, f"{arquivo.name} referencia codigo da aplicacao"


def test_there_is_a_single_head(url: str) -> None:
    """Duas cabecas significam branches de migracao que alguem esqueceu de unir -- e
    `upgrade head` passa a falhar com uma mensagem que nao explica nada."""
    resultado = alembic("heads", url=url)

    cabecas = [linha for linha in resultado.stdout.splitlines() if "(head)" in linha]
    assert len(cabecas) == 1, f"esperava uma cabeca, achei {len(cabecas)}: {resultado.stdout}"
