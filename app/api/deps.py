"""Dependencias injetaveis nas rotas.

Nenhum endpoint deve alcancar um objeto global diretamente. Tudo que uma rota precisa
chega por injecao, a partir do estado da aplicacao montada pela factory. Isso mantem
`create_app(settings)` honesta -- a configuracao passada e a configuracao usada -- e
permite substituir qualquer dependencia em teste.

Nas proximas versoes este modulo recebe a sessao de banco e o provider de LLM.
"""

from typing import Annotated, cast

from fastapi import Depends
from starlette.requests import Request

from app.core.config import Settings


def get_app_settings(request: Request) -> Settings:
    """Configuracao efetiva desta instancia da aplicacao."""
    return cast(Settings, request.app.state.settings)


def get_correlation_id(request: Request) -> str | None:
    """Identificador do request atual, definido pelo CorrelationIdMiddleware."""
    return cast(str | None, getattr(request.state, "correlation_id", None))


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
CorrelationIdDep = Annotated[str | None, Depends(get_correlation_id)]
