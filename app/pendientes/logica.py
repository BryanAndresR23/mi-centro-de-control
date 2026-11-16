"""Pendientes y metas: urgencia, orden de la vista Hoy, recurrencias, rachas y progreso. Funciones puras."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from ..tiempo import sumar_meses, ultimo_dia

URGENCIAS = {0: "Vencida", 1: "Hoy", 2: "Mañana", 3: "Esta semana", 4: "Más adelante", 5: "Sin fecha"}


@dataclass(frozen=True)
class TareaDato:
    id: int
    fecha_limite: date | None
    prioridad: int  # 1 alta, 2 media, 3 baja
    estado: str = "pendiente"


def urgencia(fecha_limite: date | None, hoy: date) -> int:
    if fecha_limite is None:
        return 5
    dias = (fecha_limite - hoy).days
    if dias < 0:
        return 0
    if dias == 0:
        return 1
    if dias == 1:
        return 2
    if dias <= 7:
        return 3
    return 4


def ordenar_tareas(tareas: Iterable[TareaDato], hoy: date) -> list[TareaDato]:
    """Primero lo vencido y lo de hoy; dentro de cada grupo, la prioridad y luego la fecha."""
    abiertas = [t for t in tareas if t.estado in ("pendiente", "en_curso")]
    return sorted(abiertas, key=lambda t: (urgencia(t.fecha_limite, hoy), t.prioridad, t.fecha_limite or date.max, t.id))


def siguiente_ocurrencia(fecha: date, recurrencia: str | None) -> date | None:
    if recurrencia == "diaria":
        return fecha + timedelta(days=1)
    if recurrencia == "semanal":
        return fecha + timedelta(days=7)
    if recurrencia == "mensual":
        anio, mes = sumar_meses(fecha.year, fecha.month, 1)
        return date(anio, mes, min(fecha.day, ultimo_dia(anio, mes).day))
    return None


def racha(dias_con_actividad: Iterable[date], hoy: date) -> int:
    """Días seguidos con actividad hasta hoy. Si hoy aún no hay, la racha de ayer sigue viva."""
    dias = set(dias_con_actividad)
    cursor = hoy if hoy in dias else hoy - timedelta(days=1)
    total = 0
    while cursor in dias:
        total += 1
        cursor -= timedelta(days=1)
    return total


def inicio_semana(hoy: date) -> date:
    return hoy - timedelta(days=hoy.weekday())


def minutos_semana(sesiones: Iterable[tuple[date, int]], hoy: date) -> int:
    lunes = inicio_semana(hoy)
    return sum(minutos for fecha, minutos in sesiones if lunes <= fecha <= hoy)


def uno_rm(peso: Decimal, repeticiones: int) -> Decimal:
    """Repetición máxima estimada (fórmula de Epley)."""
    if repeticiones <= 1:
        return Decimal(peso)
    return (Decimal(peso) * (1 + Decimal(repeticiones) / 30)).quantize(Decimal("0.1"), ROUND_HALF_UP)


def mejores_marcas(series: Sequence[tuple[date, str, int, Decimal | None]]) -> dict[str, tuple[Decimal, date]]:
    """Por ejercicio: el mayor 1RM estimado y su fecha."""
    marcas: dict[str, tuple[Decimal, date]] = {}
    for fecha, ejercicio, reps, peso in series:
        if peso is None or reps <= 0:
            continue
        clave = ejercicio.strip().lower()
        valor = uno_rm(peso, reps)
        if clave not in marcas or valor > marcas[clave][0]:
            marcas[clave] = (valor, fecha)
    return marcas


def progreso_hitos(estados: Sequence[str]) -> tuple[int, int]:
    """(hechos, total) sin contar los cancelados."""
    validos = [e for e in estados if e != "cancelada"]
    return sum(1 for e in validos if e == "hecha"), len(validos)


def periodo_actual(hoy: date, horizonte: str) -> str:
    return str(hoy.year) if horizonte == "anual" else f"{hoy.year}-T{(hoy.month - 1) // 3 + 1}"


def embudo(estados: Iterable[str]) -> dict[str, int]:
    conteo = {e: 0 for e in ("postulado", "entrevista", "oferta", "rechazo", "retirado")}
    for e in estados:
        conteo[e] = conteo.get(e, 0) + 1
    return conteo
