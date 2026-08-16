"""Testes do tipo de coluna de data e hora."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.db.types import UtcDateTime, utcnow

TIPO = UtcDateTime()


def test_utcnow_is_timezone_aware() -> None:
    assert utcnow().tzinfo is not None


def test_naive_datetime_is_rejected_on_write() -> None:
    """Falhar na gravacao e melhor que gravar um instante ambiguo.

    Um datetime sem fuso pode ser 14h em Sao Paulo ou 14h em UTC -- tres horas de
    diferenca, silenciosas, em toda metrica de duracao calculada depois.
    """
    with pytest.raises(ValueError, match="fuso horario"):
        TIPO.process_bind_param(datetime(2026, 8, 16, 14, 0), None)  # type: ignore[arg-type]


def test_other_timezones_are_converted_to_utc_on_write() -> None:
    horario_de_brasilia = timezone(timedelta(hours=-3))
    momento = datetime(2026, 8, 16, 11, 0, tzinfo=horario_de_brasilia)

    stored = TIPO.process_bind_param(momento, None)  # type: ignore[arg-type]

    assert stored == datetime(2026, 8, 16, 14, 0)  # 11h em Brasilia = 14h UTC


def test_value_read_back_carries_utc() -> None:
    stored = datetime(2026, 8, 16, 14, 0)

    loaded = TIPO.process_result_value(stored, None)  # type: ignore[arg-type]

    assert loaded is not None
    assert loaded.tzinfo is not None
    assert loaded == datetime(2026, 8, 16, 14, 0, tzinfo=UTC)


def test_none_passes_through() -> None:
    assert TIPO.process_bind_param(None, None) is None  # type: ignore[arg-type]
    assert TIPO.process_result_value(None, None) is None  # type: ignore[arg-type]


def test_round_trip_preserves_the_instant() -> None:
    original = utcnow()

    recovered = TIPO.process_result_value(
        TIPO.process_bind_param(original, None),  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
    )

    assert recovered == original
