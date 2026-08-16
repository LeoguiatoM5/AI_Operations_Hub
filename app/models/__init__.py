"""Modelos de persistencia.

Importados aqui para que `Base.metadata` conheca todas as tabelas quando o schema for
criado -- um modelo nunca importado simplesmente nao existiria no banco.
"""

from app.models.document import Document
from app.models.enums import DocumentStatus, ExecutionStatus
from app.models.execution import AgentExecution, Execution

__all__ = [
    "AgentExecution",
    "Document",
    "DocumentStatus",
    "Execution",
    "ExecutionStatus",
]
