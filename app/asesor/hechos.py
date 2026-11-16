"""Hechos del asesor: números calculados por el código a partir de tus datos (la IA solo los redacta).

Todas las funciones son puras: reciben movimientos y datos simples, y devuelven una lista de hechos
{id, tipo, texto, impacto_usd, prioridad} que se guardan junto al informe.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from ..finanzas.calculos import CERO, Mov, categoria_raiz, tasa_ahorro
from ..finanzas.montos import formato_porcentaje, formato_usd

UMBRAL_HORMIGA = Decimal("10.00")
NO_DISCRECIONALES = {"Vivienda", "Servicios básicos", "Salud", "Educación", "Impuestos", "Comisiones e intereses",
                     "Internet y celular", "Supermercado", "Transporte"}
INGRESOS_EXTRA = {"Décimo tercero", "Décimo cuarto", "Utilidades", "Fondos de reserva", "Trabajos extra"}


@dataclass(frozen=True)
class MovDetalle(Mov):
    comercio: str | None = None


def _q(valor: Decimal) -> Decimal:
    return Decimal(valor).quantize(Decimal("0.01"), ROUND_HALF_UP)


def gasto_por_raiz(movs: Sequence[Mov], desde: date, hasta: date, padre_de: dict) -> dict[int | None, Decimal]:
    totales: dict[int | None, Decimal] = defaultdict(lambda: CERO)
    for m in movs:
        if m.tipo == "gasto" and desde <= m.fecha <= hasta:
            totales[categoria_raiz(m.categoria_id, padre_de)] += m.monto
    return totales


def comparar_categorias(
    movs: Sequence[Mov], periodo: tuple[date, date], base: tuple[date, date], padre_de: dict, nombres: dict[int, str],
) -> list[dict]:
    """Categorías cuyo gasto subió o bajó ≥ 25 % (y al menos $10) frente al promedio de la base, ajustado por días."""
    dias_periodo = (periodo[1] - periodo[0]).days + 1
    dias_base = (base[1] - base[0]).days + 1
    actual = gasto_por_raiz(movs, *periodo, padre_de)
    previo = gasto_por_raiz(movs, *base, padre_de)
    hechos = []
    for cat_id in set(actual) | set(previo):
        esperado = _q(previo.get(cat_id, CERO) / dias_base * dias_periodo)
        real = _q(actual.get(cat_id, CERO))
        diferencia = real - esperado
        if esperado <= 0 or abs(diferencia) < 10:
            continue
        cambio = diferencia / esperado
        nombre = nombres.get(cat_id, "Sin categoría")
        if cambio >= Decimal("0.25"):
            hechos.append({"tipo": "categoria_sube", "categoria": nombre, "impacto_usd": str(diferencia),
                           "texto": f"Gastaste {formato_porcentaje(cambio)} más en {nombre} que tu promedio "
                                    f"({formato_usd(real)} frente a {formato_usd(esperado)}).", "prioridad": 1})
        elif cambio <= Decimal("-0.25") and real > 0:  # dejar de gastar en algo ocasional no es un logro
            hechos.append({"tipo": "categoria_baja", "categoria": nombre, "impacto_usd": str(-diferencia),
                           "texto": f"Gastaste {formato_porcentaje(-cambio)} menos en {nombre} que tu promedio "
                                    f"({formato_usd(real)} frente a {formato_usd(esperado)}).", "prioridad": 3})
    return sorted(hechos, key=lambda h: Decimal(h["impacto_usd"]), reverse=True)


def gastos_hormiga(movs: Sequence[MovDetalle], periodo: tuple[date, date], umbral: Decimal = UMBRAL_HORMIGA) -> dict | None:
    pequenos = [m for m in movs if m.tipo == "gasto" and periodo[0] <= m.fecha <= periodo[1] and m.monto < umbral]
    if len(pequenos) < 6:
        return None
    total = sum((m.monto for m in pequenos), CERO)
    dias = (periodo[1] - periodo[0]).days + 1
    anual = _q(total / dias * 365)
    top = [c for c, _ in Counter((m.comercio or "sin nombre") for m in pequenos).most_common(3)]
    return {"tipo": "hormiga", "impacto_usd": str(_q(total / dias * 30)),
            "texto": f"Hiciste {len(pequenos)} compras de menos de {formato_usd(umbral)} por {formato_usd(total)} "
                     f"(sobre todo en {', '.join(top)}). A este ritmo son {formato_usd(anual)} al año.", "prioridad": 2}


def resumen_ahorro(movs: Sequence[Mov], periodo: tuple[date, date], base: tuple[date, date]) -> dict | None:
    def sumas(desde, hasta):
        ing = sum((m.monto for m in movs if m.tipo == "ingreso" and desde <= m.fecha <= hasta), CERO)
        gas = sum((m.monto for m in movs if m.tipo == "gasto" and desde <= m.fecha <= hasta), CERO)
        return ing, gas

    ing, gas = sumas(*periodo)
    tasa = tasa_ahorro(ing, gas)
    tasa_base = tasa_ahorro(*sumas(*base))
    if tasa is None:
        return None
    texto = f"Tu tasa de ahorro fue {formato_porcentaje(tasa)} (ingresos {formato_usd(ing)}, gastos {formato_usd(gas)})"
    if tasa_base is not None:
        texto += f"; tu promedio reciente es {formato_porcentaje(tasa_base)}"
    return {"tipo": "tasa_ahorro", "impacto_usd": str(_q(ing - gas)), "texto": texto + ".",
            "prioridad": 1 if tasa < 0 else 2 if tasa_base is not None and tasa < tasa_base else 3}


def ingresos_extra(movs: Sequence[Mov], periodo: tuple[date, date], nombres: dict[int, str]) -> dict | None:
    extra = [(nombres.get(m.categoria_id, ""), m.monto) for m in movs
             if m.tipo == "ingreso" and periodo[0] <= m.fecha <= periodo[1] and nombres.get(m.categoria_id) in INGRESOS_EXTRA]
    if not extra:
        return None
    total = sum((monto for _, monto in extra), CERO)
    return {"tipo": "ingreso_extra", "impacto_usd": str(total), "prioridad": 1,
            "texto": f"Recibiste {formato_usd(total)} de ingresos extraordinarios ({', '.join(sorted({n for n, _ in extra}))})."}


def metas_y_recortes(
    metas: Sequence[tuple[str, Decimal, Decimal, Decimal, date | None, int | None]],
    movs: Sequence[Mov], hoy: date, padre_de: dict, nombres: dict[int, str],
) -> list[dict]:
    """metas: (nombre, objetivo, total, ritmo_mensual, fecha_objetivo, meses_estimados).
    Calcula cuántos meses antes llegas si recortas 20 % de tu categoría discrecional más grande."""
    desde = hoy - timedelta(days=90)
    por_cat = gasto_por_raiz(movs, desde, hoy, padre_de)
    discrecionales = sorted(((c, t) for c, t in por_cat.items() if nombres.get(c) and nombres[c] not in NO_DISCRECIONALES),
                            key=lambda par: par[1], reverse=True)
    recorte_cat, recorte = (None, CERO)
    if discrecionales:
        recorte_cat, total = discrecionales[0]
        recorte = _q(total / 3 * Decimal("0.20"))
    hechos = []
    for nombre, objetivo, total, ritmo, fecha_obj, meses in metas:
        faltante = objetivo - total
        if faltante <= 0:
            hechos.append({"tipo": "meta_cumplida", "impacto_usd": None, "prioridad": 3,
                           "texto": f"¡Cumpliste la meta «{nombre}» ({formato_usd(total)})!"})
            continue
        if ritmo <= 0:
            hechos.append({"tipo": "meta_sin_ritmo", "impacto_usd": str(_q(faltante)), "prioridad": 2,
                           "texto": f"La meta «{nombre}» no recibió aportes en los últimos 3 meses; le faltan {formato_usd(faltante)}."})
            continue
        texto = f"A tu ritmo de {formato_usd(ritmo)}/mes llegas a «{nombre}» en {meses} meses."
        if recorte > 0 and recorte_cat is not None:
            nuevos = math.ceil(faltante / (ritmo + recorte))
            if nuevos < (meses or nuevos + 1):
                texto += (f" Si recortas 20 % de {nombres[recorte_cat]} ({formato_usd(recorte)}/mes) y lo ahorras, "
                          f"llegas en {nuevos} meses ({(meses or nuevos) - nuevos} antes).")
        if fecha_obj and meses is not None:
            meses_disponibles = max((fecha_obj.year - hoy.year) * 12 + fecha_obj.month - hoy.month, 1)
            if meses > meses_disponibles:
                necesario = _q(faltante / meses_disponibles)
                texto += f" Para llegar al {fecha_obj:%d/%m/%Y} necesitas {formato_usd(necesario)}/mes."
        hechos.append({"tipo": "meta", "impacto_usd": str(recorte) if recorte else None, "texto": texto, "prioridad": 2})
    return hechos


def suscripciones(subs: Sequence[tuple[str, Decimal, int]]) -> list[dict]:
    """subs: (nombre, costo mensual, meses sin uso)."""
    if not subs:
        return []
    total = sum((c for _, c, _ in subs), CERO)
    hechos = [{"tipo": "suscripciones", "impacto_usd": str(total), "prioridad": 3,
               "texto": f"Pagas {formato_usd(total)}/mes en {len(subs)} suscripciones ({formato_usd(total * 12)} al año)."}]
    for nombre, costo, sin_uso in subs:
        if sin_uso >= 2:
            hechos.append({"tipo": "suscripcion_sin_uso", "impacto_usd": str(costo), "prioridad": 1,
                           "texto": f"Llevas {sin_uso} meses sin usar {nombre}: cancelarla ahorra {formato_usd(costo * 12)} al año."})
    return hechos


def numerar(hechos: list[dict]) -> list[dict]:
    return [{**h, "id": f"h{i + 1}"} for i, h in enumerate(sorted(hechos, key=lambda h: h.get("prioridad", 3)))]
