"""Presupuestos, metas de ahorro y pagos recurrentes: funciones puras, probadas sin base de datos."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from ..tiempo import primer_dia, sumar_meses, ultimo_dia
from .calculos import CERO, Mov, categoria_raiz

UMBRAL_ALERTA = Decimal("0.80")
UMBRAL_EXCEDIDO = Decimal("1.00")


# --- Presupuestos -------------------------------------------------------------


@dataclass(frozen=True)
class PresupuestoDato:
    id: int
    categoria_id: int
    monto: Decimal
    desde_mes: date
    hasta_mes: date | None = None

    def vigente_en(self, anio: int, mes: int) -> bool:
        inicio = primer_dia(anio, mes)
        return self.desde_mes <= inicio and (self.hasta_mes is None or self.hasta_mes >= inicio)


@dataclass(frozen=True)
class EstadoPresupuesto:
    presupuesto_id: int
    categoria_id: int
    monto: Decimal
    gastado: Decimal
    proyectado: Decimal | None  # solo para el mes en curso: a este ritmo, cuánto gastarás

    @property
    def pct(self) -> Decimal:
        return (self.gastado / self.monto).quantize(Decimal("0.0001")) if self.monto else CERO

    @property
    def restante(self) -> Decimal:
        return self.monto - self.gastado

    @property
    def nivel(self) -> str:
        return nivel_presupuesto(self.pct)


def nivel_presupuesto(pct: Decimal) -> str:
    if pct >= UMBRAL_EXCEDIDO:
        return "excedido"
    if pct >= UMBRAL_ALERTA:
        return "alerta"
    return "ok"


def gastado_por_categoria_raiz(
    movs: Iterable[Mov], desde: date, hasta: date, padre_de: dict[int, int | None]
) -> dict[int | None, Decimal]:
    totales: dict[int | None, Decimal] = {}
    for m in movs:
        if m.tipo == "gasto" and desde <= m.fecha <= hasta:
            raiz = categoria_raiz(m.categoria_id, padre_de)
            totales[raiz] = totales.get(raiz, CERO) + m.monto
    return totales


def estado_presupuestos(
    presupuestos: Sequence[PresupuestoDato],
    movs: Sequence[Mov],
    anio: int,
    mes: int,
    padre_de: dict[int, int | None],
    hoy: date,
) -> list[EstadoPresupuesto]:
    """Cómo va cada presupuesto vigente en el mes, del más consumido al menos."""
    desde, hasta = primer_dia(anio, mes), ultimo_dia(anio, mes)
    gastado = gastado_por_categoria_raiz(movs, desde, hasta, padre_de)
    es_mes_actual = (anio, mes) == (hoy.year, hoy.month)
    estados = []
    for p in presupuestos:
        if not p.vigente_en(anio, mes):
            continue
        total = gastado.get(p.categoria_id, CERO)
        proyectado = None
        if es_mes_actual and hoy.day < hasta.day:
            proyectado = (total / hoy.day * hasta.day).quantize(Decimal("0.01"), ROUND_HALF_UP)
        estados.append(EstadoPresupuesto(p.id, p.categoria_id, p.monto, total, proyectado))
    return sorted(estados, key=lambda e: e.pct, reverse=True)


def umbral_cruzado(antes: Decimal, despues: Decimal, monto: Decimal) -> Decimal | None:
    """Si un gasto hizo pasar el presupuesto del 80 % o del 100 %, devuelve ese umbral (el mayor)."""
    if monto <= 0:
        return None
    for umbral in (UMBRAL_EXCEDIDO, UMBRAL_ALERTA):
        if antes / monto < umbral <= despues / monto:
            return umbral
    return None


def promedio_mensual(movs: Iterable[Mov], categoria_id: int, padre_de: dict[int, int | None], hoy: date, meses: int = 3) -> Decimal:
    """Gasto promedio de los últimos `meses` meses completos en una categoría principal (sugerencia de presupuesto)."""
    a, m = sumar_meses(hoy.year, hoy.month, -meses)
    desde = primer_dia(a, m)
    a2, m2 = sumar_meses(hoy.year, hoy.month, -1)
    hasta = ultimo_dia(a2, m2)
    total = gastado_por_categoria_raiz(movs, desde, hasta, padre_de).get(categoria_id, CERO)
    return (total / meses).quantize(Decimal("0.01"), ROUND_HALF_UP)


# --- Metas de ahorro ----------------------------------------------------------


def aporte_a_meta(tipo: str, monto: Decimal) -> Decimal:
    """Ahorros (transferencias) e ingresos asignados a la meta suman; un gasto pagado con la meta resta."""
    if tipo in ("transferencia", "ingreso"):
        return monto
    if tipo == "gasto":
        return -monto
    return CERO


@dataclass(frozen=True)
class ProgresoMeta:
    total: Decimal
    objetivo: Decimal
    ritmo_mensual: Decimal  # aporte promedio de los últimos 3 meses
    meses_estimados: int | None
    fecha_estimada: date | None
    cuota_necesaria: Decimal | None  # cuánto ahorrar por mes para llegar a la fecha objetivo
    fecha_objetivo: date | None

    @property
    def pct(self) -> Decimal:
        return (self.total / self.objetivo).quantize(Decimal("0.0001")) if self.objetivo else CERO

    @property
    def faltante(self) -> Decimal:
        return max(self.objetivo - self.total, CERO)

    @property
    def cumplida(self) -> bool:
        return self.total >= self.objetivo

    @property
    def en_camino(self) -> bool | None:
        if self.cumplida:
            return True
        if not self.fecha_objetivo or not self.fecha_estimada:
            return None if not self.fecha_objetivo else False
        return self.fecha_estimada <= self.fecha_objetivo


def meses_entre(desde: date, hasta: date) -> int:
    """Meses calendario completos o parciales entre dos fechas (mínimo 1 si hasta > desde)."""
    meses = (hasta.year - desde.year) * 12 + (hasta.month - desde.month)
    if hasta.day > desde.day:
        meses += 1
    return max(meses, 1) if hasta > desde else 0


def progreso_meta(
    objetivo: Decimal,
    saldo_inicial: Decimal,
    aportes: Sequence[tuple[date, Decimal]],
    hoy: date,
    fecha_objetivo: date | None = None,
) -> ProgresoMeta:
    total = saldo_inicial + sum((monto for _, monto in aportes), CERO)
    hace_90 = hoy - timedelta(days=90)
    ritmo = (sum((m for f, m in aportes if hace_90 < f <= hoy), CERO) / 3).quantize(Decimal("0.01"), ROUND_HALF_UP)
    faltante = max(objetivo - total, CERO)

    meses_estimados = fecha_estimada = None
    if faltante == 0:
        meses_estimados, fecha_estimada = 0, hoy
    elif ritmo > 0:
        meses_estimados = math.ceil(faltante / ritmo)
        a, m = sumar_meses(hoy.year, hoy.month, meses_estimados)
        fecha_estimada = date(a, m, min(hoy.day, ultimo_dia(a, m).day))

    cuota = None
    if fecha_objetivo and faltante > 0 and fecha_objetivo > hoy:
        cuota = (faltante / meses_entre(hoy, fecha_objetivo)).quantize(Decimal("0.01"), ROUND_HALF_UP)
    return ProgresoMeta(total, objetivo, ritmo, meses_estimados, fecha_estimada, cuota, fecha_objetivo)


# --- Pagos recurrentes --------------------------------------------------------


def siguiente_fecha(fecha: date, frecuencia: str, dia: int) -> date:
    """El vencimiento siguiente. `dia` es el día original: 31 → 28/29 en febrero → 31 en marzo."""
    if frecuencia == "semanal":
        return fecha + timedelta(days=7)
    if frecuencia == "anual":
        anio, mes = fecha.year + 1, fecha.month
    elif frecuencia == "mensual":
        anio, mes = sumar_meses(fecha.year, fecha.month, 1)
    else:
        raise ValueError(f"Frecuencia desconocida: {frecuencia}")
    return date(anio, mes, min(dia, ultimo_dia(anio, mes).day))


def costo_mensual(monto: Decimal, frecuencia: str) -> Decimal:
    if frecuencia == "anual":
        valor = monto / 12
    elif frecuencia == "semanal":
        valor = monto * 52 / 12
    else:
        valor = monto
    return valor.quantize(Decimal("0.01"), ROUND_HALF_UP)


@dataclass(frozen=True)
class PagoProximo:
    recurrente_id: int
    fecha: date
    monto: Decimal
    dias: int  # negativo = vencido

    @property
    def estado(self) -> str:
        if self.dias < 0:
            return "vencido"
        if self.dias == 0:
            return "hoy"
        return "pronto" if self.dias <= 3 else "programado"


def proximos_pagos(
    recurrentes: Iterable[tuple[int, date, Decimal]], hoy: date, horizonte_dias: int = 30
) -> list[PagoProximo]:
    """(id, próxima fecha, monto) → pagos vencidos o dentro del horizonte, del más urgente al menos."""
    pagos = [
        PagoProximo(rid, fecha, monto, (fecha - hoy).days)
        for rid, fecha, monto in recurrentes
        if (fecha - hoy).days <= horizonte_dias
    ]
    return sorted(pagos, key=lambda p: (p.fecha, p.recurrente_id))
