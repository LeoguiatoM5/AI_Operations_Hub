"""Registro de ferramentas.

Um catalogo com nome unico por ferramenta, responsavel por tres coisas que nao devem
ficar espalhadas: validar argumentos antes de executar, medir a execucao, e responder
quem precisa de aprovacao humana.

O registro e montado por requisicao, como o grafo, porque as ferramentas carregam
dependencias do request (o retriever ja traz a sessao do banco vetorial). Montar e
barato: e um dicionario.
"""

import re
import time
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from app.core.exceptions import AIHubError, ConfigurationError
from app.core.logging import get_logger
from app.tools.base import Tool, ToolResult, ToolSpec
from app.tools.exceptions import ToolExecutionError, ToolInputError, ToolNotFoundError

logger = get_logger(__name__)

#: Nomes em snake_case minusculo. A restricao existe porque o nome viaja por lugares
#: menos tolerantes que o Python: prompt de LLM, JSON de plano, e o protocolo MCP no V6.
TOOL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,63}$")


class ToolRegistry:
    """Catalogo de ferramentas disponiveis para uma execucao."""

    def __init__(self, tools: Sequence[Tool[Any]] = ()) -> None:
        self._tools: dict[str, Tool[Any]] = {}
        for tool in tools:
            self.register(tool)

    # ------------------------------------------------------------------ catalogo

    def register(self, tool: Tool[Any]) -> None:
        """Adiciona uma ferramenta ao catalogo.

        Nome invalido ou repetido derruba o registro na montagem, e nao no momento em que
        um agente tentar usar a ferramenta errada. Duas ferramentas com o mesmo nome sao
        especialmente perigosas: a segunda substituiria a primeira em silencio, e o
        sistema passaria a executar outra acao sem nenhum sinal.
        """
        if not TOOL_NAME_PATTERN.fullmatch(tool.name):
            raise ConfigurationError(
                f"Nome de ferramenta invalido: {tool.name!r}. "
                "Use minusculas, digitos e underscore, de 3 a 64 caracteres."
            )
        if tool.name in self._tools:
            raise ConfigurationError(f"Ja existe uma ferramenta registrada como {tool.name!r}.")

        self._tools[tool.name] = tool
        logger.debug("tool_registered", tool=tool.name, scope=tool.scope.value)

    def get(self, name: str) -> Tool[Any]:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolNotFoundError(
                f"A ferramenta {name!r} nao existe.",
                details={"tool": name, "available": sorted(self._tools)},
            )
        return tool

    def specs(self) -> list[ToolSpec]:
        """Catalogo em formato de dados, ordenado por nome.

        Ordenado porque essa lista entra em prompt: ordem estavel mantem o prompt estavel,
        e prompt estavel e o que permite comparar duas execucoes.
        """
        return [
            ToolSpec(
                name=tool.name,
                description=tool.description,
                scope=tool.scope,
                requires_approval=tool.scope.requires_approval,
                input_schema=tool.input_model.model_json_schema(),
            )
            for tool in sorted(self._tools.values(), key=lambda item: item.name)
        ]

    def requires_approval(self, name: str) -> bool:
        """Se esta ferramenta exige aprovacao humana antes de executar."""
        return self.get(name).scope.requires_approval

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def __iter__(self) -> Iterator[Tool[Any]]:
        return iter(self._tools.values())

    # ------------------------------------------------------------------ execucao

    def validate_input(self, name: str, arguments: Mapping[str, Any]) -> BaseModel:
        """Valida os argumentos sem executar nada.

        Metodo separado de proposito: no fluxo de aprovacao, a validacao acontece ANTES
        de o grafo pausar. Pedir a um humano que aprove argumentos que seriam rejeitados
        depois desperdica o tempo dele e deixa no banco um registro aprovado que nunca
        podera ser executado.
        """
        # Anotacao explicita: o registro guarda `Tool[Any]`, entao sem ela o resultado da
        # validacao chegaria como `Any` e o tipo se perderia daqui para cima.
        modelo: type[BaseModel] = self.get(name).input_model
        try:
            return modelo.model_validate(dict(arguments))
        except PydanticValidationError as error:
            raise ToolInputError(
                f"Argumentos invalidos para a ferramenta {name!r}.",
                # A lista de erros vai junto porque e ela que alimenta o retry dirigido
                # (mesmo mecanismo do ED-023): o modelo precisa saber O QUE errou.
                details={"tool": name, "errors": error.errors(include_url=False)},
            ) from error

    async def execute(self, name: str, arguments: Mapping[str, Any]) -> ToolResult:
        """Valida, executa e mede.

        Os argumentos entram como dicionario, e nao como modelo ja validado, porque no
        fluxo de aprovacao eles passam pelo banco em JSON entre a pausa e a retomada.
        Revalidar na saida do banco e barato e fecha a porta para um payload adulterado
        entre a aprovacao e a execucao.
        """
        tool = self.get(name)
        payload = self.validate_input(name, arguments)

        inicio = time.perf_counter()
        try:
            resultado = await tool.run(payload)
        except AIHubError:
            # Erro do dominio ja tem codigo e status corretos: sobe como esta.
            raise
        except Exception as error:
            # Um SDK de terceiro pode levantar qualquer coisa. A traducao acontece aqui,
            # na fronteira, para que os nos do grafo lidem apenas com AIHubError.
            raise ToolExecutionError(
                f"A ferramenta {name!r} falhou: {error}",
                details={"tool": name, "error_type": type(error).__name__},
            ) from error

        decorrido = round((time.perf_counter() - inicio) * 1000, 3)

        logger.info(
            "tool_executed",
            tool=name,
            scope=tool.scope.value,
            latency_ms=decorrido,
            # Os argumentos NAO sao logados: um payload de ferramenta carrega o conteudo
            # da mensagem, o destinatario, as vezes um token. O rastro auditavel dele e a
            # tabela de aprovacoes, com controle de acesso -- nao o log de aplicacao.
        )

        # A latencia e carimbada aqui, e nao dentro da ferramenta, para que toda
        # ferramenta seja medida do mesmo jeito sem que o autor precise lembrar disso.
        return resultado.model_copy(update={"latency_ms": decorrido})
