"""Divisao de texto em pedacos indexaveis.

O tamanho do pedaco e a decisao mais subestimada de um RAG:

- grande demais dilui o sinal -- o vetor medio de 2000 tokens nao representa bem nenhum
  trecho especifico, e a busca fica imprecisa;
- pequeno demais perde contexto -- recupera-se "o prazo e de 30 dias" sem saber prazo de
  que, e o modelo responde com confianca sobre a coisa errada.

A sobreposicao existe para que uma frase cortada no limite de um pedaco continue inteira
no pedaco seguinte.
"""

import re
from uuid import uuid4

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.rag.base import Chunk, ChunkMetadata

#: Ordem de preferencia dos pontos de corte. O divisor tenta o primeiro separador; se os
#: pedacos ainda ficarem grandes, desce para o proximo. Cortar em paragrafo preserva mais
#: sentido que cortar em espaco, e cortar em espaco preserva mais que cortar no meio de
#: uma palavra.
SEPARATORS = ["\n\n", "\n", ". ", "? ", "! ", "; ", ", ", " ", ""]

_WHITESPACE = re.compile(r"[ \t]+")
_BLANK_LINES = re.compile(r"\n{3,}")


def normalize_text(text: str) -> str:
    """Remove ruido que atrapalha tanto o corte quanto o embedding.

    PDFs e paginas web trazem espacos repetidos e sequencias de linhas em branco que nao
    carregam significado, mas consomem tokens e deslocam os limites dos pedacos.
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = _WHITESPACE.sub(" ", normalized)
    normalized = _BLANK_LINES.sub("\n\n", normalized)
    return normalized.strip()


def split_text(text: str, *, chunk_size: int, chunk_overlap: int) -> list[str]:
    """Divide o texto respeitando limites naturais sempre que possivel.

    Usa o divisor recursivo do `langchain-text-splitters`: peca testada e amplamente
    usada, encapsulada aqui para que trocar de estrategia (por sentenca, por titulo
    markdown, semantica) nao toque em nenhum outro modulo.
    """
    if chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap precisa ser menor que chunk_size.")

    cleaned = normalize_text(text)
    if not cleaned:
        return []

    splitter = RecursiveCharacterTextSplitter(
        separators=SEPARATORS,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        keep_separator=True,
        strip_whitespace=True,
    )
    return [piece for piece in splitter.split_text(cleaned) if piece.strip()]


def build_chunks(
    text: str,
    *,
    document_id: str,
    chunk_size: int,
    chunk_overlap: int,
    metadata: ChunkMetadata | None = None,
) -> list[Chunk]:
    """Divide o texto e monta os pedacos ja com identidade e metadados.

    Os metadados sao copiados para cada pedaco porque e neles que a busca filtra: o
    banco vetorial nao consegue fazer junção com a tabela de documentos.
    """
    base_metadata = dict(metadata or {})
    pieces = split_text(text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)

    return [
        Chunk(
            id=uuid4().hex,
            document_id=document_id,
            text=piece,
            index=position,
            metadata={**base_metadata, "document_id": document_id, "chunk_index": position},
        )
        for position, piece in enumerate(pieces)
    ]
