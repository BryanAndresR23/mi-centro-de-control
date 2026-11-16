"""Fechas en hora de Quito y utilidades de meses."""

from __future__ import annotations

import calendar
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

QUITO = ZoneInfo("America/Guayaquil")

MESES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]
MESES_CORTOS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
DIAS_SEMANA = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def ahora_quito() -> datetime:
    return datetime.now(QUITO)


def hoy() -> date:
    return ahora_quito().date()


def ahora_utc() -> datetime:
    """UTC sin zona horaria: se guarda igual en SQLite y en PostgreSQL."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def primer_dia(anio: int, mes: int) -> date:
    return date(anio, mes, 1)


def ultimo_dia(anio: int, mes: int) -> date:
    return date(anio, mes, calendar.monthrange(anio, mes)[1])


def sumar_meses(anio: int, mes: int, n: int) -> tuple[int, int]:
    indice = anio * 12 + (mes - 1) + n
    return indice // 12, indice % 12 + 1


def meses_hasta(anio: int, mes: int, cantidad: int) -> list[tuple[int, int]]:
    """Los últimos `cantidad` meses que terminan en (anio, mes), del más antiguo al más reciente."""
    return [sumar_meses(anio, mes, -i) for i in range(cantidad - 1, -1, -1)]


def parse_mes(texto: str | None, por_defecto: date) -> tuple[int, int]:
    """'2026-11' -> (2026, 11). Si el texto no sirve, usa el mes de `por_defecto`."""
    if texto:
        try:
            anio_txt, mes_txt = texto.split("-", 1)
            anio, mes = int(anio_txt), int(mes_txt)
            if 2000 <= anio <= 2100 and 1 <= mes <= 12:
                return anio, mes
        except ValueError:
            pass
    return por_defecto.year, por_defecto.month


def nombre_mes(anio: int, mes: int) -> str:
    return f"{MESES[mes - 1]} {anio}"


def fecha_larga(f: date) -> str:
    return f"{DIAS_SEMANA[f.weekday()]} {f.day} de {MESES[f.month - 1]}"


def fecha_corta(f: date) -> str:
    return f"{f.day} {MESES_CORTOS[f.month - 1]} {f.year}"
