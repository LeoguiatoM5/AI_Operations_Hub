"""Schemas do catalogo de ferramentas."""

from pydantic import BaseModel, Field

from app.tools.base import ToolSpec


class ToolCatalogResponse(BaseModel):
    """Ferramentas que esta instancia sabe executar.

    Reaproveita `ToolSpec` do dominio em vez de copiar os campos. A regra do projeto e
    separar schema de API de modelo de dominio, e ela existe para que uma mudanca interna
    nao vaze para o contrato publico -- mas `ToolSpec` ja e um DTO puro, sem
    comportamento e sem acoplamento com banco. Uma copia identica aqui nao protegeria
    nada: apenas criaria dois lugares para editar e um deles para esquecer.
    """

    total: int = Field(description="Quantidade de ferramentas registradas.")
    tools: list[ToolSpec] = Field(default_factory=list)
    write_tools: list[str] = Field(
        default_factory=list,
        description="Ferramentas de escrita -- as unicas que exigem aprovacao humana.",
    )
