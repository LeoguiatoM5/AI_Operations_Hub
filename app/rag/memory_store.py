"""Banco vetorial em memoria.

Nao e um brinquedo de teste: e a implementacao de referencia. Toda a busca semantica
cabe em vinte linhas de similaridade do cosseno, e ter isso escrito de forma legivel
serve a dois propositos -- testes rapidos sem dependencia externa, e a possibilidade de
explicar exatamente o que um banco vetorial faz por baixo.

O que ele NAO faz e o que justifica o Chroma: persistencia em disco e indice aproximado
(HNSW). Aqui a busca e exaustiva, O(n) por consulta.
"""

import math
from collections.abc import Sequence

from app.rag.base import Chunk, SearchHit, Vector


def cosine_similarity(left: Vector, right: Vector) -> float:
    """Cosseno do angulo entre dois vetores, em [-1, 1].

    Mede orientacao, nao magnitude: dois textos sobre o mesmo assunto ficam alinhados
    mesmo com tamanhos bem diferentes. E por isso que se usa cosseno, e nao distancia
    euclidiana, para comparar embeddings de texto.
    """
    if len(left) != len(right):
        raise ValueError(
            f"Vetores de dimensoes diferentes: {len(left)} e {len(right)}. "
            "Indice e consulta precisam usar o mesmo modelo de embedding."
        )

    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norm_left = math.sqrt(sum(a * a for a in left))
    norm_right = math.sqrt(sum(b * b for b in right))
    if norm_left == 0 or norm_right == 0:
        return 0.0
    return dot / (norm_left * norm_right)


class InMemoryVectorStore:
    """Indice exaustivo mantido em memoria."""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[Chunk, list[float]]] = {}

    @property
    def name(self) -> str:
        return "memory"

    async def add_chunks(self, chunks: Sequence[Chunk], vectors: Sequence[Vector]) -> None:
        if len(chunks) != len(vectors):
            raise ValueError(
                f"Quantidade incompativel: {len(chunks)} pedacos e {len(vectors)} vetores."
            )
        for chunk, vector in zip(chunks, vectors, strict=True):
            self._entries[chunk.id] = (chunk, list(vector))

    async def search(
        self,
        vector: Vector,
        *,
        top_k: int = 5,
        document_ids: Sequence[str] | None = None,
    ) -> list[SearchHit]:
        allowed = set(document_ids) if document_ids else None

        scored: list[SearchHit] = []
        for chunk, stored in self._entries.values():
            if allowed is not None and chunk.document_id not in allowed:
                continue
            similarity = cosine_similarity(vector, stored)
            scored.append(
                SearchHit(
                    chunk_id=chunk.id,
                    document_id=chunk.document_id,
                    text=chunk.text,
                    # Embeddings de texto raramente sao opostos, mas o cosseno permite
                    # valores negativos. Cortamos em zero para manter o contrato [0, 1].
                    score=max(0.0, min(1.0, similarity)),
                    metadata=chunk.metadata,
                )
            )

        scored.sort(key=lambda hit: hit.score, reverse=True)
        return scored[:top_k]

    async def delete_document(self, document_id: str) -> int:
        alvos = [
            chunk_id
            for chunk_id, (chunk, _) in self._entries.items()
            if chunk.document_id == document_id
        ]
        for chunk_id in alvos:
            del self._entries[chunk_id]
        return len(alvos)

    async def count(self) -> int:
        return len(self._entries)

    async def reset(self) -> None:
        self._entries.clear()

    async def aclose(self) -> None:
        return None
