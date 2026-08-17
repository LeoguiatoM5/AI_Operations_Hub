"""Contrato de uma ferramenta.

Uma ferramenta e um Protocol como os demais pontos de extensao do projeto
(`LLMProvider`, `VectorStore`): nome, descricao, escopo, o modelo Pydantic da sua
entrada, e um metodo assincrono que executa.

O detalhe que carrega a seguranca do V4 esta em `ToolScope`. O escopo e declarado pela
FERRAMENTA, nao pelo chamador. Se fosse o chamador a informar, bastaria um no do grafo
esquecer o parametro para uma acao irreversivel passar sem aprovacao -- e o esquecimento
nao apareceria em nenhum teste, porque o codigo continuaria correto do ponto de vista de
tipos.
"""

from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class ToolScope(StrEnum):
    """O que a ferramenta faz com o mundo externo."""

    #: Apenas consulta. Reversivel por natureza: nao muda nada.
    READ = "read"
    #: Altera estado fora do sistema (envia, cria, apaga). Pode nao ter desfazer.
    WRITE = "write"

    @property
    def requires_approval(self) -> bool:
        """A regra de aprovacao humana, em um lugar so.

        Escrita aqui, e nao nos nos do grafo nem nos endpoints, porque uma regra de
        seguranca duplicada e uma regra que vai divergir. Quem quiser saber se uma acao
        precisa de humano pergunta ao escopo -- e nao ha segunda resposta possivel.
        """
        return self is ToolScope.WRITE


class ToolResult(BaseModel):
    """O que uma ferramenta devolve quando da certo.

    Nao ha campo `ok`: falha nao vira resultado com bandeira, vira excecao
    (`ToolExecutionError`). Um resultado com `ok=False` e facil de ignorar por acidente;
    uma excecao obriga quem chama a decidir o que fazer.
    """

    tool: str
    #: Frase curta, legivel por humano. E o que aparece na tela de aprovacao e no
    #: relatorio final -- por isso e obrigatoria, e nao um extra opcional.
    summary: str
    output: dict[str, Any] = Field(default_factory=dict)
    #: Preenchido pelo registro, nao pela ferramenta (ver `ToolRegistry.execute`).
    latency_ms: float = Field(default=0.0, ge=0)


class ToolSpec(BaseModel):
    """Descricao de uma ferramenta sem a implementacao.

    Serve a tres consumidores diferentes pelo mesmo objeto: o prompt do agente de
    automacao (que precisa saber o que existe), a API (que expoe o catalogo) e o servidor
    MCP do V6 (que publica ferramentas para clientes externos).
    """

    name: str
    description: str
    scope: ToolScope
    #: Derivado do escopo. Redundante de proposito: quem le o catalogo pela API ve a
    #: consequencia sem precisar conhecer a regra.
    requires_approval: bool
    input_schema: dict[str, Any]


@runtime_checkable
class Tool[T: BaseModel](Protocol):
    """Uma acao que o sistema sabe executar."""

    @property
    def name(self) -> str:
        """Identificador estavel, usado pelo plano do agente e pelo registro."""
        ...

    @property
    def description(self) -> str:
        """Uma frase, escrita para o LLM ler: quando usar esta ferramenta."""
        ...

    @property
    def scope(self) -> ToolScope: ...

    @property
    def input_model(self) -> type[T]:
        """Modelo Pydantic dos argumentos.

        Mesmo principio do ED-022, na direcao oposta: la, saida de LLM nunca circula como
        dicionario solto; aqui, argumento GERADO por LLM nunca chega a um sistema externo
        sem passar por um modelo validado.
        """
        ...

    async def run(self, payload: T) -> ToolResult:
        """Executa a acao. Recebe o payload ja validado pelo registro."""
        ...
