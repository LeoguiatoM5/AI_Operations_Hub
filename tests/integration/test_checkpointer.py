"""Testes do checkpointer persistente do grafo.

Este arquivo existe por causa de um defeito real. O restante da suite injeta
`MemorySaver`, o que e correto -- testes nao devem tocar disco. A consequencia era que o
caminho de PRODUCAO, onde o `AsyncSqliteSaver` e criado no lifespan, nunca era exercitado.

O `aiosqlite` 0.22 deixou de herdar de `threading.Thread` e removeu `is_alive()`, que o
`langgraph-checkpoint-sqlite` chama em `setup()`. A instalacao continuava funcionando e a
aplicacao nao subia. Nenhum teste falhava.

A licao vale alem deste caso: **substituto em teste que nunca e confrontado com o real
esconde incompatibilidade de dependencia**. Um teste do componente verdadeiro, ainda que
lento, e o que fecha essa porta.
"""

import operator
from pathlib import Path
from typing import Annotated, Any, TypedDict

import pytest
from langgraph.graph import END, START, StateGraph

from app.core.config import Settings
from app.workflows.checkpointer import create_checkpointer


class EstadoSimples(TypedDict):
    passos: Annotated[list[str], operator.add]


async def primeiro(_state: EstadoSimples) -> dict[str, Any]:
    return {"passos": ["primeiro"]}


async def segundo(_state: EstadoSimples) -> dict[str, Any]:
    return {"passos": ["segundo"]}


def grafo_de_teste() -> StateGraph[EstadoSimples, None, EstadoSimples, EstadoSimples]:
    builder: StateGraph[EstadoSimples, None, EstadoSimples, EstadoSimples] = StateGraph(
        EstadoSimples
    )
    builder.add_node("primeiro", primeiro)
    builder.add_node("segundo", segundo)
    builder.add_edge(START, "primeiro")
    builder.add_edge("primeiro", "segundo")
    builder.add_edge("segundo", END)
    return builder


@pytest.fixture
def config(temp_dir: Path) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        app_env="test",
        log_level="ERROR",
        checkpoint_path=str(temp_dir / "checkpoints.db"),
    )


async def test_checkpointer_is_created_and_usable(config: Settings) -> None:
    """O teste que teria pego as duas incompatibilidades de dependencia."""
    _saver, connection = await create_checkpointer(config)
    try:
        assert Path(config.checkpoint_path).exists()  # noqa: ASYNC240
    finally:
        await connection.close()


async def test_graph_state_survives_the_run(config: Settings) -> None:
    saver, connection = await create_checkpointer(config)
    try:
        graph = grafo_de_teste().compile(checkpointer=saver)
        run_config = {"configurable": {"thread_id": "execucao-1"}}

        resultado = await graph.ainvoke({"passos": []}, config=run_config)  # type: ignore[arg-type]

        assert resultado["passos"] == ["primeiro", "segundo"]

        # O ponto do checkpointer: o estado continua acessivel DEPOIS da execucao.
        gravado = await graph.aget_state(run_config)  # type: ignore[arg-type]
        assert gravado.values["passos"] == ["primeiro", "segundo"]
    finally:
        await connection.close()


async def test_state_is_recovered_by_a_new_saver_instance(config: Settings) -> None:
    """Persistencia de verdade: outro processo consegue ler o estado do disco.

    E isso que vai permitir, no V4, uma aprovacao humana retomar um grafo pausado apos o
    servidor ter sido reiniciado.
    """
    saver, connection = await create_checkpointer(config)
    run_config = {"configurable": {"thread_id": "execucao-2"}}
    try:
        await (
            grafo_de_teste()
            .compile(checkpointer=saver)
            .ainvoke(
                {"passos": []},
                config=run_config,  # type: ignore[arg-type]
            )
        )
    finally:
        await connection.close()

    outro_saver, outra_conexao = await create_checkpointer(config)
    try:
        graph = grafo_de_teste().compile(checkpointer=outro_saver)
        recuperado = await graph.aget_state(run_config)  # type: ignore[arg-type]

        assert recuperado.values["passos"] == ["primeiro", "segundo"]
    finally:
        await outra_conexao.close()


async def test_threads_are_isolated(config: Settings) -> None:
    """Cada execucao tem o proprio thread_id: uma nao enxerga o estado da outra."""
    saver, connection = await create_checkpointer(config)
    try:
        graph = grafo_de_teste().compile(checkpointer=saver)
        await graph.ainvoke({"passos": []}, config={"configurable": {"thread_id": "a"}})  # type: ignore[arg-type]

        outra = await graph.aget_state({"configurable": {"thread_id": "b"}})  # type: ignore[arg-type]

        assert outra.values == {}
    finally:
        await connection.close()
