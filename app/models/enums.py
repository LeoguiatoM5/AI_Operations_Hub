"""Enumeracoes do dominio."""

from enum import StrEnum


class ExecutionStatus(StrEnum):
    """Ciclo de vida de uma execucao.

    `StrEnum` porque o valor precisa ser legivel no banco e serializavel em JSON sem
    conversao. Um inteiro economizaria bytes e custaria clareza em toda consulta manual.
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

    #: Aguardando aprovacao humana antes de executar uma acao de escrita (V4).
    WAITING_APPROVAL = "waiting_approval"

    #: Reprovada pelo AI Quality Gateway e encaminhada para revisao humana (V5).
    NEEDS_HUMAN_REVIEW = "needs_human_review"

    @property
    def is_terminal(self) -> bool:
        """Estados a partir dos quais a execucao nao avanca sozinha."""
        return self in {ExecutionStatus.COMPLETED, ExecutionStatus.FAILED}


class DocumentStatus(StrEnum):
    """Ciclo de vida de um documento na base de conhecimento.

    O estado existe porque a ingestao escreve em DOIS sistemas que nao compartilham
    transacao: o banco relacional (metadados) e o banco vetorial (trechos). Sem status
    explicito, um processo interrompido no meio deixaria um documento registrado e nao
    indexado -- invisivel na busca, sem erro algum.
    """

    PENDING = "pending"
    PROCESSING = "processing"
    INDEXED = "indexed"
    FAILED = "failed"

    @property
    def is_searchable(self) -> bool:
        """Somente documentos indexados participam da busca."""
        return self is DocumentStatus.INDEXED
