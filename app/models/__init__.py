"""Modelos de persistencia.

Importados aqui para que `Base.metadata` conheca todas as tabelas quando o schema for
criado -- um modelo nunca importado simplesmente nao existiria no banco.
"""

from app.models.approval import Approval
from app.models.document import Document
from app.models.enums import ApprovalStatus, DocumentStatus, ExecutionStatus
from app.models.execution import AgentExecution, Execution

__all__ = [
    "AgentExecution",
    "Approval",
    "ApprovalStatus",
    "Document",
    "DocumentStatus",
    "Execution",
    "ExecutionStatus",
]
