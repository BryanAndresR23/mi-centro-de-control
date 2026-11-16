"""Cálculos financieros como funciones puras: reciben datos y devuelven números.

No tocan la base de datos, así que se prueban sin ella (tests/test_calculos.py).

Convención de saldos: todas las cuentas usan un "saldo contable" donde lo que tienes
es positivo y lo que debes es negativo. Así:
  - un gasto con tarjeta baja su saldo (la deuda crece),
  - pagar la tarjeta es una transferencia que lo sube (la deuda baja),
  - el patrimonio neto es simplemente la suma de todos los saldos.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from ..tiempo import primer_dia, ultimo_dia

CERO = Decimal("0.00")
TIPOS_PASIVO = frozenset({"tarjeta_credito", "prestamo"})


@dataclass(frozen=True)
class Mov:
    fecha: date
    tipo: str  # ingreso | gasto | transferencia
    monto: Decimal
    cuenta_id: int
    cuenta_destino_id: int | None = None
    categoria_id: int | None = None


@dataclass(frozen=True)
class CuentaSaldo:
    id: int
    tipo: str
    saldo_inicial: Decimal  # en pasivos: lo que se debe, en positivo
    fecha_saldo_inicial: date  # saldo al inicio de ese día

    @property
    def es_pasivo(self) -> bool:
        return self.tipo in TIPOS_PASIVO

    @property
    def saldo_inicial_contable(self) -> Decimal:
        return -self.saldo_inicial if self.es_pasivo else self.saldo_inicial


@dataclass(frozen=True)
class Resumen:
    ingresos: Decimal
    gastos: Decimal

    @property
    def flujo(self) -> Decimal:
        return self.ingresos - self.gastos

    @property
    def tasa_ahorro(self) -> Decimal | None:
        return tasa_ahorro(self.ingresos, self.gastos)


def tasa_ahorro(ingresos: Decimal, gastos: Decimal) -> Decimal | None:
    """(ingresos - gastos) / ingresos. None si no hubo ingresos."""
    if ingresos <= 0:
        return None
    return ((ingresos - gastos) / ingresos).quantize(Decimal("0.0001"))


def efecto_en_cuenta(m: Mov, cuenta_id: int) -> Decimal:
    """Cuánto cambia el saldo contable de `cuenta_id` por el movimiento `m`."""
    if m.tipo == "ingreso":
        return m.monto if m.cuenta_id == cuenta_id else CERO
    if m.tipo == "gasto":
        return -m.monto if m.cuenta_id == cuenta_id else CERO
    if m.tipo == "transferencia":
        efecto = CERO
        if m.cuenta_id == cuenta_id:
            efecto -= m.monto
        if m.cuenta_destino_id == cuenta_id:
            efecto += m.monto
        return efecto
    raise ValueError(f"Tipo de movimiento desconocido: {m.tipo}")


def saldo_al(cuenta: CuentaSaldo, movs: Iterable[Mov], corte: date) -> Decimal:
    """Saldo contable al final del día `corte`.

    El saldo inicial es el saldo al empezar el día `fecha_saldo_inicial`:
      - desde esa fecha en adelante se suman los movimientos;
      - para fechas anteriores se "retrocede" restando los movimientos intermedios.
    Así puedes importar historia anterior sin descuadrar el saldo de hoy.
    """
    inicio = cuenta.fecha_saldo_inicial
    saldo = cuenta.saldo_inicial_contable
    for m in movs:
        efecto = efecto_en_cuenta(m, cuenta.id)
        if not efecto:
            continue
        if corte >= inicio:
            if inicio <= m.fecha <= corte:
                saldo += efecto
        elif corte < m.fecha < inicio:
            saldo -= efecto
    return saldo


def saldos_al(cuentas: Sequence[CuentaSaldo], movs: Sequence[Mov], corte: date) -> dict[int, Decimal]:
    return {c.id: saldo_al(c, movs, corte) for c in cuentas}


def patrimonio_neto(cuentas: Sequence[CuentaSaldo], movs: Sequence[Mov], corte: date) -> Decimal:
    return sum(saldos_al(cuentas, movs, corte).values(), CERO)


def activos_y_pasivos(
    cuentas: Sequence[CuentaSaldo], movs: Sequence[Mov], corte: date
) -> tuple[Decimal, Decimal]:
    """(activos, pasivos). Los pasivos se devuelven en positivo (lo que debes)."""
    saldos = saldos_al(cuentas, movs, corte)
    activos = sum((saldos[c.id] for c in cuentas if not c.es_pasivo), CERO)
    pasivos = -sum((saldos[c.id] for c in cuentas if c.es_pasivo), CERO)
    return activos, pasivos


def resumen_periodo(movs: Iterable[Mov], desde: date, hasta: date) -> Resumen:
    """Ingresos y gastos entre dos fechas (inclusive). Las transferencias no cuentan:
    mover dinero entre tus cuentas, ahorrar o pagar la tarjeta no es ingreso ni gasto."""
    ingresos = gastos = CERO
    for m in movs:
        if desde <= m.fecha <= hasta:
            if m.tipo == "ingreso":
                ingresos += m.monto
            elif m.tipo == "gasto":
                gastos += m.monto
    return Resumen(ingresos, gastos)


def resumen_mes(movs: Iterable[Mov], anio: int, mes: int) -> Resumen:
    return resumen_periodo(movs, primer_dia(anio, mes), ultimo_dia(anio, mes))


def categoria_raiz(categoria_id: int | None, padre_de: dict[int, int | None]) -> int | None:
    """Sube por las categorías padre hasta la principal (con tope contra ciclos)."""
    actual = categoria_id
    for _ in range(10):
        padre = padre_de.get(actual) if actual is not None else None
        if padre is None:
            return actual
        actual = padre
    return actual


def gasto_por_categoria(
    movs: Iterable[Mov], desde: date, hasta: date, padre_de: dict[int, int | None] | None = None
) -> list[tuple[int | None, Decimal]]:
    """Gasto total por categoría principal, de mayor a menor."""
    padre_de = padre_de or {}
    totales: dict[int | None, Decimal] = defaultdict(lambda: CERO)
    for m in movs:
        if m.tipo == "gasto" and desde <= m.fecha <= hasta:
            totales[categoria_raiz(m.categoria_id, padre_de)] += m.monto
    return sorted(totales.items(), key=lambda par: par[1], reverse=True)


def serie_mensual(movs: Iterable[Mov], meses: Sequence[tuple[int, int]]) -> list[Resumen]:
    """Ingresos y gastos de cada mes pedido, en el mismo orden."""
    ingresos: dict[tuple[int, int], Decimal] = defaultdict(lambda: CERO)
    gastos: dict[tuple[int, int], Decimal] = defaultdict(lambda: CERO)
    for m in movs:
        clave = (m.fecha.year, m.fecha.month)
        if m.tipo == "ingreso":
            ingresos[clave] += m.monto
        elif m.tipo == "gasto":
            gastos[clave] += m.monto
    return [Resumen(ingresos[clave], gastos[clave]) for clave in meses]


def serie_patrimonio(
    cuentas: Sequence[CuentaSaldo], movs: Sequence[Mov], meses: Sequence[tuple[int, int]], hoy: date
) -> list[Decimal]:
    """Patrimonio neto al cierre de cada mes (o a hoy, para el mes en curso)."""
    return [patrimonio_neto(cuentas, movs, min(ultimo_dia(a, m), hoy)) for a, m in meses]


def variacion(actual: Decimal, anterior: Decimal) -> Decimal | None:
    """Cambio relativo: 0.25 = +25%. None si no hay base de comparación."""
    if anterior <= 0:
        return None
    return ((actual - anterior) / anterior).quantize(Decimal("0.0001"))
