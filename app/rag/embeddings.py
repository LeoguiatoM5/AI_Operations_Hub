"""Provedores de embedding.

Mesmo padrao da camada de LLM: um Protocol, uma implementacao real, uma deterministica
para teste, e um decorador de retry aplicado por fora.
"""

import hashlib
import math
import re
import unicodedata
from collections.abc import Sequence
from time import perf_counter

import openai
from openai import AsyncOpenAI

from app.core.logging import get_logger
from app.core.retry import RetryPolicy, retry_async
from app.llm.base import TokenUsage
from app.llm.exceptions import (
    LLMAuthenticationError,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from app.llm.pricing import estimate_cost_usd
from app.rag.base import EmbeddingProvider, EmbeddingResult, EmbeddingUsage

logger = get_logger(__name__)

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")

#: Palavras com menos de 3 letras sao, em portugues, majoritariamente conectivos
#: ("de", "a", "o", "em", "no"). Elas aparecem em todo texto e, com contagem bruta,
#: passariam a dominar a similaridade: um documento com tres "de" ficaria mais proximo
#: da pergunta que o documento que trata do assunto perguntado. Descartar as curtas e a
#: versao mais simples do que o IDF faz em um vetorizador de verdade.
_MIN_TOKEN_LENGTH = 3


# --------------------------------------------------------------------------- fake


def _tokenize(text: str) -> list[str]:
    """Normaliza acentuacao e quebra em palavras.

    Sem a normalizacao, "reembolso" e "reembôlso" virariam palavras distintas e a busca
    perderia correspondencias obvias em portugues.
    """
    lowered = unicodedata.normalize("NFKD", text.lower())
    without_accents = "".join(char for char in lowered if not unicodedata.combining(char))
    return _TOKEN_PATTERN.findall(without_accents)


def _content_tokens(text: str) -> set[str]:
    """Palavras que carregam assunto, sem repeticoes.

    Presenca binaria em vez de contagem: repetir uma palavra dez vezes nao torna o texto
    dez vezes mais sobre ela, e a contagem bruta distorce a comparacao entre textos de
    tamanhos diferentes.
    """
    return {token for token in _tokenize(text) if len(token) >= _MIN_TOKEN_LENGTH}


def _stable_bucket(token: str, dimensions: int) -> int:
    """Posicao estavel de uma palavra no vetor.

    `hash()` de string em Python e aleatorizado por processo (PYTHONHASHSEED). Usa-lo
    aqui produziria vetores diferentes a cada execucao -- indice gravado hoje ficaria
    incompativel com a busca de amanha, e nenhum teste seria reproduzivel.
    """
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % dimensions


class FakeEmbeddingProvider:
    """Vetorizacao por hashing, deterministica e sem rede.

    Nao produz vetores aleatorios: cada palavra de conteudo ocupa sempre a mesma posicao,
    marcada por presenca e depois normalizada. O resultado e que a similaridade do
    cosseno reflete sobreposicao real de vocabulario -- buscar "politica de reembolso"
    recupera de fato o trecho sobre reembolso.

    Isso torna possivel testar comportamento de RAG, e nao apenas encanamento, sem gastar
    um centavo nem depender de rede.

    O que ele nao faz: capturar sinonimia. "carro" e "automovel" continuam distantes.
    Para isso existe o provedor real.
    """

    def __init__(self, *, dimensions: int = 256) -> None:
        self._dimensions = dimensions
        self.calls = 0

    @property
    def name(self) -> str:
        return "fake"

    @property
    def model(self) -> str:
        return "fake-embedding-1"

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def min_relevant_score(self) -> float:
        """Piso baixo, e de proposito.

        A separacao entre acerto e erro na busca lexical e ruim: nesta base, o melhor
        acerto ficou em 0.250 e um erro chegou a 0.167. Nao existe corte que separe bem
        os dois. Um piso alto esconderia isso atras de "sem contexto suficiente"; um piso
        baixo deixa a fraqueza visivel, que e o comportamento honesto para um provedor
        cuja funcao e testar.
        """
        return 0.05

    def _vectorize(self, text: str) -> list[float]:
        vector = [0.0] * self._dimensions
        for token in _content_tokens(text):
            vector[_stable_bucket(token, self._dimensions)] += 1.0

        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [value / norm for value in vector]

    async def embed_documents(self, texts: Sequence[str]) -> EmbeddingResult:
        self.calls += 1
        return EmbeddingResult(
            vectors=[self._vectorize(text) for text in texts],
            model=self.model,
            usage=EmbeddingUsage(tokens=sum(len(_tokenize(text)) for text in texts)),
        )

    async def embed_query(self, text: str) -> EmbeddingResult:
        return await self.embed_documents([text])

    async def aclose(self) -> None:
        return None


# --------------------------------------------------------------------------- openai


class OpenAIEmbeddingProvider:
    """Embeddings da OpenAI."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        dimensions: int,
        timeout_seconds: float = 30.0,
        min_relevant_score: float = 0.35,
    ) -> None:
        self._model = model
        self._dimensions = dimensions
        self._min_relevant_score = min_relevant_score
        # max_retries=0 pelo mesmo motivo da camada de LLM: o retry e nosso, medido.
        self._client = AsyncOpenAI(api_key=api_key, timeout=timeout_seconds, max_retries=0)

    @property
    def name(self) -> str:
        return "openai"

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def min_relevant_score(self) -> float:
        """Medido nesta base: acertos entre 0.59 e 0.77, erros entre 0.38 e 0.42.

        O padrao de 0.35 e conservador -- corta o ruido evidente sem descartar contexto
        secundario legitimo. O valor definitivo sai da medicao com o conjunto de
        avaliacao (V5), nao de intuicao.
        """
        return self._min_relevant_score

    async def _embed(self, texts: Sequence[str]) -> EmbeddingResult:
        started_at = perf_counter()
        try:
            response = await self._client.embeddings.create(
                model=self._model,
                input=list(texts),
                dimensions=self._dimensions,
            )
        except openai.APITimeoutError as error:
            raise LLMTimeoutError(details={"model": self._model}) from error
        except openai.RateLimitError as error:
            raise LLMRateLimitError(details={"model": self._model}) from error
        except openai.AuthenticationError as error:
            raise LLMAuthenticationError(details={"model": self._model}) from error
        except (openai.APIStatusError, openai.APIConnectionError) as error:
            raise LLMError("Falha ao gerar embeddings.", details={"model": self._model}) from error

        latency_ms = round((perf_counter() - started_at) * 1000, 3)
        tokens = response.usage.prompt_tokens if response.usage else 0

        return EmbeddingResult(
            vectors=[item.embedding for item in response.data],
            model=response.model or self._model,
            usage=EmbeddingUsage(
                tokens=tokens,
                cost_usd=estimate_cost_usd(
                    response.model or self._model, TokenUsage(prompt_tokens=tokens)
                ),
                latency_ms=latency_ms,
            ),
        )

    async def embed_documents(self, texts: Sequence[str]) -> EmbeddingResult:
        return await self._embed(texts)

    async def embed_query(self, text: str) -> EmbeddingResult:
        return await self._embed([text])

    async def aclose(self) -> None:
        await self._client.close()


# --------------------------------------------------------------------------- retry


class RetryingEmbeddingProvider:
    """Aplica backoff exponencial a falhas transitorias, sem tocar nas implementacoes."""

    def __init__(self, inner: EmbeddingProvider, policy: RetryPolicy) -> None:
        self._inner = inner
        self._policy = policy

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def model(self) -> str:
        return self._inner.model

    @property
    def dimensions(self) -> int:
        return self._inner.dimensions

    @property
    def min_relevant_score(self) -> float:
        return self._inner.min_relevant_score

    @staticmethod
    def _is_retryable(error: Exception) -> bool:
        return isinstance(error, LLMError) and error.retryable

    async def embed_documents(self, texts: Sequence[str]) -> EmbeddingResult:
        return await retry_async(
            lambda: self._inner.embed_documents(texts),
            policy=self._policy,
            should_retry=self._is_retryable,
            operation_name=f"embeddings.documents[{self._inner.name}]",
        )

    async def embed_query(self, text: str) -> EmbeddingResult:
        return await retry_async(
            lambda: self._inner.embed_query(text),
            policy=self._policy,
            should_retry=self._is_retryable,
            operation_name=f"embeddings.query[{self._inner.name}]",
        )

    async def aclose(self) -> None:
        await self._inner.aclose()
