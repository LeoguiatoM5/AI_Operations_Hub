"""Endpoint de saude.

Serve a dois publicos: o healthcheck do Docker/orquestrador e o desenvolvedor que
quer confirmar qual versao e qual ambiente estao no ar.
"""

from time import monotonic

from fastapi import APIRouter, Request

from app import __version__
from app.api.deps import SettingsDep
from app.schemas.health import HealthResponse

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Verifica se a aplicacao esta no ar",
)
async def health(request: Request, settings: SettingsDep) -> HealthResponse:
    started_at: float = request.app.state.started_at
    return HealthResponse(
        status="ok",
        app=settings.app_name,
        version=__version__,
        environment=settings.app_env,
        uptime_seconds=round(monotonic() - started_at, 3),
    )
