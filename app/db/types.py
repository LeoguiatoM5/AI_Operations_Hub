"""Tipos de coluna personalizados."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, Dialect, String
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


class StrEnumType[T: StrEnum](TypeDecorator[T]):
    """Coluna de texto que devolve o membro do enum, e nao uma string solta.

    **Por que isto existe.** Declarar `Mapped[ExecutionStatus] = mapped_column(String(32))`
    parece funcionar: grava certo, le certo, e o mypy aceita. Mas o que volta do banco e
    uma `str` -- o SQLAlchemy nao converte de volta sozinho. Enquanto o codigo so compara
    com `==`, ninguem percebe, porque `StrEnum` e comparavel a texto. O erro aparece na
    primeira vez que alguem chama um metodo do enum sobre um objeto recem-lido:
    `AttributeError: 'str' object has no attribute ...`.

    Foi assim que este tipo nasceu: `approval.status.is_decided` quebrou em runtime com o
    mypy limpo. A anotacao prometia um enum e entregava texto.

    Alternativa descartada: o tipo `Enum` do SQLAlchemy. Ele grava o NOME do membro
    (`"PENDING"`), e nao o valor (`"pending"`), a menos que se passe `values_callable` --
    o que mudaria o conteudo ja gravado. Um `TypeDecorator` mantem no banco exatamente o
    mesmo texto de antes: a coluna nao muda, so o tipo em Python passa a ser verdade.
    """

    impl = String
    cache_ok = True

    def __init__(self, enum_class: type[T], *, length: int = 32) -> None:
        self._enum_class = enum_class
        super().__init__(length=length)

    def process_bind_param(self, value: T | str | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        # Passa pelo construtor do enum de proposito: um valor invalido falha ao gravar,
        # e nao silenciosamente na leitura de quem for consultar depois.
        return self._enum_class(value).value

    def process_result_value(self, value: Any, dialect: Dialect) -> T | None:
        if value is None:
            return None
        return self._enum_class(value)


def utcnow() -> datetime:
    """Instante atual em UTC, com fuso explicito."""
    return datetime.now(UTC)
