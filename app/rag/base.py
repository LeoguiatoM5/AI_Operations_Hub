"""Contratos da camada de recuperacao.

Duas abstracoes independentes:

- `EmbeddingProvider` transforma texto em vetores;
- `VectorStore` guarda vetores e encontra os vizinhos mais proximos.

Manter as duas separadas e o que permite trocar o banco vetorial sem trocar o modelo de
embedding, e vice-versa. Bancos vetoriais costumam oferecer embedding embutido; aceitar
essa conveniencia amarraria as duas decisoes numa so.
"""

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

#: Um vetor de embedding.
Vector = Sequence[float]

#: Metadados de um chunk. Valores escalares apenas -- e o que os bancos vetoriais
#: aceitam em filtros, e o denominador comum entre eles.
ChunkMetadata = dict[str, str | int | float | bool]


class Chunk(BaseModel):
    """Um pedaco indexavel de um documento."""

    id: str
    document_id: str
    text: str
    index: int = Field(ge=0, description="Posicao do pedaco dentro do documento.")
    metadata: ChunkMetadata = Field(default_factory=dict)


class SearchHit(BaseModel):
    """Um resultado de busca.

    `score` e SIMPRE similaridade: maior e melhor, faixa [0, 1]. Bancos vetoriais
    costumam devolver distancia (menor e melhor); a conversao acontece na fronteira de
    cada implementacao, para que nenhum codigo acima precise saber disso.
    """

    chunk_id: str
    document_id: str
    text: str
    score: float = Field(ge=0.0, le=1.0)
    metadata: ChunkMetadata = Field(default_factory=dict)


class EmbeddingUsage(BaseModel):
    """Consumo de uma chamada de embedding."""

    tokens: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0)
    latency_ms: float = Field(default=0.0, ge=0)


class EmbeddingResult(BaseModel):
    """Vetores gerados e o custo de gera-los."""

    vectors: list[list[float]]
    model: str
    usage: EmbeddingUsage = Field(default_factory=EmbeddingUsage)


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Transforma texto em vetores."""

    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...

    @property
    def dimensions(self) -> int:
        """Tamanho do vetor. O banco vetorial precisa disso para validar o que recebe."""
        ...

    @property
    def min_relevant_score(self) -> float:
        """Similaridade abaixo da qual um resultado nao deve ser considerado relevante.

        Vive no provedor, e nao na configuracao global, porque a escala de similaridade
        depende do modelo. Medido nesta base: o melhor acerto do vetorizador lexical fica
        em torno de 0.25, enquanto o do `text-embedding-3-small` passa de 0.75. Um valor
        unico para os dois rejeitaria tudo em um caso e nada no outro.

        E um ponto de partida, nao uma verdade: o valor correto sai da medicao com o
        conjunto de avaliacao (V5).
        """
        ...

    async def embed_documents(self, texts: Sequence[str]) -> EmbeddingResult:
        """Vetoriza trechos que serao indexados."""
        ...

    async def embed_query(self, text: str) -> EmbeddingResult:
        """Vetoriza uma pergunta.

        Separado de `embed_documents` de proposito: varios modelos exigem prefixos
        diferentes para consulta e para documento (E5, BGE e outros). Um metodo unico
        obrigaria quem chama a saber dessa diferenca.
        """
        ...

    async def aclose(self) -> None: ...


@runtime_checkable
class VectorStore(Protocol):
    """Guarda vetores e encontra os mais proximos de um vetor de consulta."""

    @property
    def name(self) -> str: ...

    async def add_chunks(self, chunks: Sequence[Chunk], vectors: Sequence[Vector]) -> None:
        """Indexa pedacos junto com seus vetores."""
        ...

    async def search(
        self,
        vector: Vector,
        *,
        top_k: int = 5,
        document_ids: Sequence[str] | None = None,
    ) -> list[SearchHit]:
        """Devolve os pedacos mais similares, do mais para o menos similar."""
        ...

    async def delete_document(self, document_id: str) -> int:
        """Remove todos os pedacos de um documento. Devolve quantos foram removidos."""
        ...

    async def count(self) -> int:
        """Total de pedacos indexados."""
        ...

    async def reset(self) -> None:
        """Esvazia o indice."""
        ...

    async def aclose(self) -> None: ...
