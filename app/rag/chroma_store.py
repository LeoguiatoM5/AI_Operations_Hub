"""Banco vetorial ChromaDB.

Duas decisoes importantes aqui.

**Sem funcao de embedding propria.** Por padrao o Chroma baixa um modelo ONNX de dezenas
de megabytes e vetoriza sozinho. Passamos os vetores prontos: ele vira apenas o indice, o
controle do modelo de embedding continua nosso, e a primeira execucao nao depende de um
download.

**Envolvido em `asyncio.to_thread`.** O cliente do Chroma e sincrono. Chamado direto de
uma rota `async`, ele bloquearia o event loop durante a busca, travando todos os outros
requests em andamento.
"""

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.core.logging import get_logger
from app.rag.base import Chunk, ChunkMetadata, SearchHit, Vector

logger = get_logger(__name__)

#: `cosine` para casar com a metrica usada nos embeddings de texto. O padrao do Chroma e
#: distancia euclidiana, que ordenaria os resultados de forma diferente.
COLLECTION_METADATA = {"hnsw:space": "cosine"}


def _to_similarity(distance: float) -> float:
    """Converte distancia do cosseno em similaridade.

    O Chroma devolve distancia: MENOR e melhor, faixa [0, 2]. Nosso contrato e
    similaridade: MAIOR e melhor, faixa [0, 1]. Deixar a distancia vazar para cima e o
    caminho mais curto para ordenar os resultados ao contrario sem ninguem perceber.
    """
    return max(0.0, min(1.0, 1.0 - distance))


class ChromaVectorStore:
    """Indice persistente em disco."""

    def __init__(self, *, path: str, collection_name: str = "knowledge_base") -> None:
        Path(path).expanduser().resolve().mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(
            path=path,
            settings=ChromaSettings(anonymized_telemetry=False, allow_reset=True),
        )
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata=COLLECTION_METADATA,
        )
        self._collection_name = collection_name
        logger.info("chroma_ready", path=path, collection=collection_name)

    @property
    def name(self) -> str:
        return "chroma"

    async def add_chunks(self, chunks: Sequence[Chunk], vectors: Sequence[Vector]) -> None:
        if len(chunks) != len(vectors):
            raise ValueError(
                f"Quantidade incompativel: {len(chunks)} pedacos e {len(vectors)} vetores."
            )
        if not chunks:
            return

        # Anotacao explicita: `list[list[float]]` nao satisfaz `list[Sequence[float]]`
        # porque list e invariante em Python.
        payload: list[Sequence[float]] = [list(vector) for vector in vectors]

        def _add() -> None:
            self._collection.upsert(
                ids=[chunk.id for chunk in chunks],
                embeddings=payload,
                documents=[chunk.text for chunk in chunks],
                metadatas=[dict(chunk.metadata) for chunk in chunks],
            )

        await asyncio.to_thread(_add)

    async def search(
        self,
        vector: Vector,
        *,
        top_k: int = 5,
        document_ids: Sequence[str] | None = None,
    ) -> list[SearchHit]:
        where: dict[str, Any] | None = None
        if document_ids:
            where = {"document_id": {"$in": list(document_ids)}}

        query_payload: list[Sequence[float]] = [list(vector)]

        def _query() -> Any:
            return self._collection.query(
                query_embeddings=query_payload,
                n_results=top_k,
                where=where,
                include=["documents", "metadatas", "distances"],
            )

        result = await asyncio.to_thread(_query)

        ids: list[str] = (result.get("ids") or [[]])[0]
        documents: list[str] = (result.get("documents") or [[]])[0]
        metadatas: list[ChunkMetadata] = (result.get("metadatas") or [[]])[0]
        distances: list[float] = (result.get("distances") or [[]])[0]

        hits: list[SearchHit] = []
        for chunk_id, text, metadata, distance in zip(
            ids, documents, metadatas, distances, strict=True
        ):
            data = dict(metadata or {})
            hits.append(
                SearchHit(
                    chunk_id=chunk_id,
                    document_id=str(data.get("document_id", "")),
                    text=text or "",
                    score=_to_similarity(float(distance)),
                    metadata=data,
                )
            )
        return hits

    async def delete_document(self, document_id: str) -> int:
        def _delete() -> int:
            existing = self._collection.get(where={"document_id": document_id}, include=[])
            ids: list[str] = existing.get("ids") or []
            if ids:
                self._collection.delete(ids=ids)
            return len(ids)

        return await asyncio.to_thread(_delete)

    async def count(self) -> int:
        return await asyncio.to_thread(self._collection.count)

    async def reset(self) -> None:
        def _reset() -> None:
            self._client.delete_collection(self._collection_name)
            self._collection = self._client.get_or_create_collection(
                name=self._collection_name, metadata=COLLECTION_METADATA
            )

        await asyncio.to_thread(_reset)

    async def aclose(self) -> None:
        return None
