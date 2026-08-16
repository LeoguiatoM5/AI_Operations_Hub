"""Tipos de coluna personalizados."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Dialect
from sqlalchemy.types import TypeDecorator


class UtcDateTime(TypeDecorator[datetime]):
    """Data e hora sempre gravada e devolvida em UTC, com fuso explicito.

    Por que isto existe: o SQLite nao armazena fuso horario. Sem tratamento, voce grava
    um `datetime` com timezone e le um `datetime` ingenuo -- e qualquer subtracao entre
    os dois levanta `TypeError`, ou pior, produz uma duracao silenciosamente errada
    quando comparada com `datetime.now(UTC)`.

    A conversao acontece nas duas pontas:
      - ao gravar: converte para UTC e remove o fuso (o banco guarda o instante em UTC);
      - ao ler: reanexa o fuso UTC.

    No PostgreSQL a coluna ja e `TIMESTAMP WITH TIME ZONE` e o comportamento e o mesmo,
    o que mantem a migracao futura sem surpresas.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(
                "Datetime sem fuso horario nao pode ser gravado: use datetime.now(UTC)."
            )
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        stored: datetime = value
        if stored.tzinfo is None:
            return stored.replace(tzinfo=UTC)
        return stored.astimezone(UTC)


def utcnow() -> datetime:
    """Instante atual em UTC, com fuso explicito."""
    return datetime.now(UTC)
