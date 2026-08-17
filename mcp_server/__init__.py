"""Servidor MCP: o mesmo sistema, outro transporte.

REST e MCP sao adaptadores sobre a camada `services/`, que nunca soube que HTTP existia.
Nenhuma regra de negocio vive aqui.
"""

from mcp_server.container import ServiceContainer, build_container
from mcp_server.server import build_server

__all__ = ["ServiceContainer", "build_container", "build_server"]
