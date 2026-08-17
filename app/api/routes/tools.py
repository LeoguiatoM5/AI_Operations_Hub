"""Endpoint do catalogo de ferramentas.

Somente leitura. A execucao de ferramenta nao ganha endpoint proprio de proposito: uma
rota `POST /tools/{nome}/execute` seria um atalho para disparar acao de escrita sem
passar pelo fluxo de aprovacao -- exatamente o que o V4 existe para impedir.
"""

from fastapi import APIRouter

from app.api.deps import ToolRegistryDep
from app.api.responses import with_errors
from app.schemas.tools import ToolCatalogResponse
from app.tools.base import ToolScope

router = APIRouter(prefix="/tools", tags=["tools"])


@router.get(
    "",
    response_model=ToolCatalogResponse,
    summary="Lista as ferramentas disponiveis e seus escopos",
    responses=with_errors(),
)
async def list_tools(registry: ToolRegistryDep) -> ToolCatalogResponse:
    """Mostra o que o sistema sabe executar e o que exige aprovacao humana.

    `requires_approval` e derivado do escopo declarado pela propria ferramenta: nenhuma
    acao de escrita pode aparecer aqui como dispensada de aprovacao.
    """
    specs = registry.specs()
    return ToolCatalogResponse(
        total=len(specs),
        tools=specs,
        write_tools=[spec.name for spec in specs if spec.scope is ToolScope.WRITE],
    )
