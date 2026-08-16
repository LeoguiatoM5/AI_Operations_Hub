"""Contrato da camada de LLM.

Tudo acima desta camada (agentes, RAG, quality gateway) conhece apenas estes tipos.
Nenhum modulo de negocio importa um SDK de provedor.
"""

from collections.abc import Sequence
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

Role = Literal["system", "user", "assistant"]


class LLMMessage(BaseModel):
    """Uma mensagem da conversa, no formato neutro do projeto."""

    role: Role
    content: str

    @classmethod
    def system(cls, content: str) -> "LLMMessage":
        return cls(role="system", content=content)

    @classmethod
    def user(cls, content: str) -> "LLMMessage":
        return cls(role="user", content=content)

    @classmethod
    def assistant(cls, content: str) -> "LLMMessage":
        return cls(role="assistant", content=content)


class TokenUsage(BaseModel):
    """Consumo de tokens de uma chamada."""

    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class LLMResponse(BaseModel):
    """Resposta normalizada, independente do provedor.

    Carrega os dados de observabilidade junto com o conteudo: sem isso, medir custo e
    latencia exigiria instrumentar cada ponto de chamada separadamente.
    """

    content: str
    provider: str
    model: str
    usage: TokenUsage = Field(default_factory=TokenUsage)
    cost_usd: float = Field(default=0.0, ge=0)
    latency_ms: float = Field(default=0.0, ge=0)
    finish_reason: str | None = None
    attempts: int = Field(default=1, ge=1, description="Tentativas ate obter esta resposta.")


@runtime_checkable
class LLMProvider(Protocol):
    """Interface que todo provedor deve satisfazer.

    E um Protocol, e nao uma classe base abstrata: um provedor nao precisa herdar de
    nada para servir. O mypy verifica a conformidade estaticamente, sem acoplar as
    implementacoes a uma hierarquia.
    """

    @property
    def name(self) -> str:
        """Identificador do provedor, usado em logs e registros de execucao."""
        ...

    @property
    def model(self) -> str:
        """Modelo efetivamente em uso."""
        ...

    async def complete(
        self,
        messages: Sequence[LLMMessage],
        *,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        json_mode: bool = False,
    ) -> LLMResponse:
        """Executa uma chamada de completions.

        Args:
            messages: conversa em ordem cronologica.
            temperature: sobrescreve a temperatura padrao do provedor.
            max_output_tokens: teto de tokens gerados.
            json_mode: exige que a resposta seja um objeto JSON valido.

        Raises:
            LLMError: qualquer falha de comunicacao, traduzida do SDK do provedor.
        """
        ...

    async def aclose(self) -> None:
        """Libera recursos (conexoes HTTP mantidas abertas pelo SDK)."""
        ...
