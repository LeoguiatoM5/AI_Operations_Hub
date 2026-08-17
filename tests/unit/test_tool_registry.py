"""Testes do registro de ferramentas.

O que esta sendo protegido aqui nao e conveniencia de API: e a regra que decide o que um
humano precisa autorizar. Um defeito nesta classe nao aparece como erro -- aparece como
uma acao irreversivel executada sem aprovacao.
"""

import pytest
from pydantic import BaseModel, Field

from app.core.exceptions import AIHubError, ConfigurationError
from app.tools.base import ToolResult, ToolScope
from app.tools.exceptions import ToolExecutionError, ToolInputError, ToolNotFoundError
from app.tools.registry import ToolRegistry


class EntradaSimples(BaseModel):
    texto: str = Field(min_length=1, max_length=50)


class FalhaDeDominioError(AIHubError):
    code = "falha_de_dominio"
    http_status = 409


class FerramentaStub:
    """Ferramenta controlavel: escopo, nome e comportamento definidos pelo teste."""

    def __init__(
        self,
        *,
        name: str = "ferramenta_stub",
        scope: ToolScope = ToolScope.READ,
        raises: Exception | None = None,
    ) -> None:
        self._name = name
        self._scope = scope
        self._raises = raises
        self.chamadas: list[EntradaSimples] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "Ferramenta de teste."

    @property
    def scope(self) -> ToolScope:
        return self._scope

    @property
    def input_model(self) -> type[EntradaSimples]:
        return EntradaSimples

    async def run(self, payload: EntradaSimples) -> ToolResult:
        self.chamadas.append(payload)
        if self._raises is not None:
            raise self._raises
        return ToolResult(tool=self._name, summary=f"eco: {payload.texto}")


# --------------------------------------------------------------------- catalogo


def test_registers_and_finds_a_tool() -> None:
    registry = ToolRegistry([FerramentaStub()])

    assert len(registry) == 1
    assert "ferramenta_stub" in registry
    assert registry.get("ferramenta_stub").name == "ferramenta_stub"


def test_duplicate_name_fails_on_registration() -> None:
    """A segunda substituiria a primeira em silencio -- e o sistema passaria a executar
    outra acao sem nenhum sinal."""
    with pytest.raises(ConfigurationError, match="Ja existe"):
        ToolRegistry([FerramentaStub(), FerramentaStub()])


@pytest.mark.parametrize(
    "nome", ["Maiuscula", "com-hifen", "ab", "com espaco", "1comeca_com_digito"]
)
def test_invalid_name_fails_on_registration(nome: str) -> None:
    """O nome viaja por prompt, JSON de plano e MCP: falhar na montagem e o barato."""
    with pytest.raises(ConfigurationError, match="invalido"):
        ToolRegistry([FerramentaStub(name=nome)])


def test_unknown_tool_lists_what_exists() -> None:
    """O erro precisa dizer o que existe: quem errou o nome foi um LLM, e a lista e o
    que permite corrigir o prompt sem adivinhacao."""
    registry = ToolRegistry([FerramentaStub()])

    with pytest.raises(ToolNotFoundError) as erro:
        registry.get("nao_existe")

    assert erro.value.details["available"] == ["ferramenta_stub"]


def test_catalog_is_sorted_by_name() -> None:
    """Ordem estavel mantem o prompt estavel, e prompt estavel permite comparar execucoes."""
    registry = ToolRegistry(
        [FerramentaStub(name="zeta"), FerramentaStub(name="alfa"), FerramentaStub(name="meio")]
    )

    assert [spec.name for spec in registry.specs()] == ["alfa", "meio", "zeta"]


def test_catalog_publishes_the_input_schema() -> None:
    registry = ToolRegistry([FerramentaStub()])

    spec = registry.specs()[0]

    assert spec.input_schema["properties"]["texto"]["maxLength"] == 50


# --------------------------------------------------------------------- escopo


def test_write_scope_requires_approval() -> None:
    registry = ToolRegistry([FerramentaStub(name="escreve", scope=ToolScope.WRITE)])

    assert registry.requires_approval("escreve") is True
    assert registry.specs()[0].requires_approval is True


def test_read_scope_does_not_require_approval() -> None:
    """Sem este caso, um defeito que exigisse aprovacao para TUDO passaria despercebido:
    a suite ficaria verde e o sistema, inutilizavel."""
    registry = ToolRegistry([FerramentaStub(name="leitura", scope=ToolScope.READ)])

    assert registry.requires_approval("leitura") is False


# --------------------------------------------------------------------- execucao


async def test_executes_with_validated_arguments() -> None:
    ferramenta = FerramentaStub()
    registry = ToolRegistry([ferramenta])

    resultado = await registry.execute("ferramenta_stub", {"texto": "ola"})

    assert resultado.summary == "eco: ola"
    assert ferramenta.chamadas[0].texto == "ola"


async def test_measures_latency_outside_the_tool() -> None:
    """A ferramenta nao preenche `latency_ms`: quem mede e o registro, para que toda
    ferramenta seja medida do mesmo jeito sem depender da memoria do autor."""
    registry = ToolRegistry([FerramentaStub()])

    resultado = await registry.execute("ferramenta_stub", {"texto": "ola"})

    assert resultado.latency_ms >= 0.0


async def test_invalid_arguments_never_reach_the_tool() -> None:
    """Argumento gerado por LLM nao chega a sistema externo sem passar pelo schema."""
    ferramenta = FerramentaStub()
    registry = ToolRegistry([ferramenta])

    with pytest.raises(ToolInputError) as erro:
        await registry.execute("ferramenta_stub", {"texto": ""})

    assert ferramenta.chamadas == []
    assert erro.value.details["errors"], "o motivo da rejeicao alimenta o retry dirigido"


def test_validation_is_available_without_executing() -> None:
    """O fluxo de aprovacao valida ANTES de pausar: aprovar argumentos que seriam
    rejeitados depois desperdica o tempo do humano."""
    ferramenta = FerramentaStub()
    registry = ToolRegistry([ferramenta])

    payload = registry.validate_input("ferramenta_stub", {"texto": "ola"})

    assert isinstance(payload, EntradaSimples)
    assert ferramenta.chamadas == []


async def test_unexpected_exception_becomes_a_domain_error() -> None:
    """Um SDK de terceiro levanta o que quiser; acima daqui so existe AIHubError."""
    registry = ToolRegistry([FerramentaStub(raises=RuntimeError("conexao caiu"))])

    with pytest.raises(ToolExecutionError) as erro:
        await registry.execute("ferramenta_stub", {"texto": "ola"})

    assert erro.value.details["error_type"] == "RuntimeError"
    assert erro.value.http_status == 502


async def test_domain_error_from_the_tool_is_preserved() -> None:
    """Traduzir um erro que ja tem codigo e status corretos so apagaria informacao."""
    registry = ToolRegistry([FerramentaStub(raises=FalhaDeDominioError("conflito"))])

    with pytest.raises(FalhaDeDominioError):
        await registry.execute("ferramenta_stub", {"texto": "ola"})
