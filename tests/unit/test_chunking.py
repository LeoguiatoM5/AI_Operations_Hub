"""Testes da divisao de texto."""

import pytest

from app.rag.chunking import build_chunks, normalize_text, split_text

TEXTO = (
    "A politica de reembolso permite solicitacoes em ate 30 dias.\n\n"
    "O prazo de analise e de 5 dias uteis apos o protocolo.\n\n"
    "Casos excepcionais passam por aprovacao da diretoria financeira."
)


def test_normalizes_line_endings_and_repeated_spaces() -> None:
    sujo = "linha um\r\n\r\n\r\n\r\nlinha    dois\t\tcom   espacos"

    assert normalize_text(sujo) == "linha um\n\nlinha dois com espacos"


def test_empty_text_produces_no_chunks() -> None:
    assert split_text("   \n\n  ", chunk_size=100, chunk_overlap=10) == []


def test_short_text_stays_in_a_single_chunk() -> None:
    pedacos = split_text("Texto curto.", chunk_size=1000, chunk_overlap=100)

    assert pedacos == ["Texto curto."]


def test_long_text_is_split() -> None:
    pedacos = split_text("frase. " * 400, chunk_size=200, chunk_overlap=20)

    assert len(pedacos) > 1
    assert all(len(pedaco) <= 220 for pedaco in pedacos)


def test_prefers_paragraph_boundaries() -> None:
    """Cortar em paragrafo preserva mais sentido que cortar no meio de uma frase."""
    pedacos = split_text(TEXTO, chunk_size=70, chunk_overlap=0)

    assert len(pedacos) == 3
    assert pedacos[0].startswith("A politica de reembolso")


def test_overlap_repeats_content_between_chunks() -> None:
    """A sobreposicao existe para que uma frase cortada no limite sobreviva inteira."""
    texto = " ".join(f"palavra{i}" for i in range(200))

    sem_overlap = split_text(texto, chunk_size=200, chunk_overlap=0)
    com_overlap = split_text(texto, chunk_size=200, chunk_overlap=80)

    assert len(com_overlap) > len(sem_overlap)


def test_overlap_must_be_smaller_than_chunk_size() -> None:
    """Sobreposicao maior que o pedaco geraria divisao infinita."""
    with pytest.raises(ValueError, match="chunk_overlap"):
        split_text(TEXTO, chunk_size=100, chunk_overlap=100)


def test_chunks_are_numbered_and_carry_the_document_id() -> None:
    pedacos = build_chunks(TEXTO, document_id="doc-1", chunk_size=70, chunk_overlap=0)

    assert [pedaco.index for pedaco in pedacos] == [0, 1, 2]
    assert all(pedaco.document_id == "doc-1" for pedaco in pedacos)
    assert len({pedaco.id for pedaco in pedacos}) == 3


def test_chunk_metadata_is_copied_to_every_piece() -> None:
    """A busca filtra pelos metadados do pedaco: o banco vetorial nao faz juncao."""
    pedacos = build_chunks(
        TEXTO,
        document_id="doc-1",
        chunk_size=70,
        chunk_overlap=0,
        metadata={"filename": "politica.md", "area": "financeiro"},
    )

    assert pedacos[0].metadata["filename"] == "politica.md"
    assert pedacos[2].metadata["area"] == "financeiro"
    assert pedacos[2].metadata["chunk_index"] == 2
