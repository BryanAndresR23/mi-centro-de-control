"""Dashboard de finanzas y página "Más"."""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from ..config import ajustes
from ..db import obtener_bd
from ..finanzas.calculos import (
    CERO,
    activos_y_pasivos,
    gasto_por_categoria,
    resumen_mes,
    resumen_periodo,
    saldos_al,
    serie_mensual,
    serie_patrimonio,
    variacion,
)
from ..finanzas.servicio import (
    asegurar_categorias_base,
    cuentas_para_calculo,
    listar_categorias,
    listar_cuentas,
    movs_para_calculo,
)
from ..models import Movimiento
from ..seguridad import requiere_login
from ..tiempo import MESES_CORTOS, hoy, meses_hasta, nombre_mes, parse_mes, primer_dia, sumar_meses, ultimo_dia
from ..web import render

router = APIRouter(dependencies=[Depends(requiere_login)])

MESES_TENDENCIA = 12


@router.get("/")
def inicio(request: Request, mes: str | None = None, bd: Session = Depends(obtener_bd)):
    asegurar_categorias_base(bd)
    hoy_ = hoy()
    anio, num_mes = parse_mes(mes, hoy_)
    desde, hasta = primer_dia(anio, num_mes), ultimo_dia(anio, num_mes)

    cuentas = listar_cuentas(bd)
    cuentas_calc = cuentas_para_calculo(cuentas)
    movs = movs_para_calculo(bd)

    resumen = resumen_mes(movs, anio, num_mes)
    anio_ant, mes_ant = sumar_meses(anio, num_mes, -1)
    es_mes_actual = (anio, num_mes) == (hoy_.year, hoy_.month)
    if es_mes_actual:
        # A mitad de mes se compara contra el mes anterior hasta el mismo día, no contra el mes completo
        fin_anterior = primer_dia(anio_ant, mes_ant).replace(day=min(hoy_.day, ultimo_dia(anio_ant, mes_ant).day))
        anterior = resumen_periodo(movs, primer_dia(anio_ant, mes_ant), fin_anterior)
        referencia = f"{fin_anterior.day} {MESES_CORTOS[mes_ant - 1]}"
    else:
        anterior = resumen_mes(movs, anio_ant, mes_ant)
        referencia = nombre_mes(anio_ant, mes_ant).split(" ")[0]

    # Gasto por categoría principal del mes, con su peso sobre el total
    categorias = {c.id: c for c in listar_categorias(bd)}
    padre_de = {c.id: c.padre_id for c in categorias.values()}
    por_categoria = []
    for cat_id, total in gasto_por_categoria(movs, desde, hasta, padre_de):
        cat = categorias.get(cat_id) if cat_id is not None else None
        por_categoria.append(
            {
                "id": cat_id,
                "nombre": cat.nombre if cat else "Sin categoría",
                "icono": cat.icono if cat else "",
                "total": total,
                "peso": (total / resumen.gastos) if resumen.gastos else Decimal(0),
            }
        )
    mayor_categoria = por_categoria[0]["total"] if por_categoria else CERO

    # Tendencia de 12 meses y patrimonio al cierre de cada mes
    meses = meses_hasta(anio, num_mes, MESES_TENDENCIA)
    serie = serie_mensual(movs, meses)
    patrimonio_mensual = serie_patrimonio(cuentas_calc, movs, meses, hoy_)
    etiquetas = [f"{MESES_CORTOS[m - 1]} {str(a)[2:]}" for a, m in meses]
    graficos = {
        "etiquetas": etiquetas,
        "ingresos": [float(r.ingresos) for r in serie],
        "gastos": [float(r.gastos) for r in serie],
        "patrimonio": [float(p) for p in patrimonio_mensual],
    }
    tabla_meses = [
        {"mes": nombre_mes(a, m), "ingresos": r.ingresos, "gastos": r.gastos, "flujo": r.flujo, "patrimonio": p}
        for (a, m), r, p in zip(meses, serie, patrimonio_mensual)
    ][::-1]
    meses_con_datos = [r for r in serie if r.ingresos or r.gastos]
    tasa_promedio = None
    if meses_con_datos:
        ingresos_total = sum((r.ingresos for r in meses_con_datos), CERO)
        gastos_total = sum((r.gastos for r in meses_con_datos), CERO)
        tasa_promedio = ((ingresos_total - gastos_total) / ingresos_total) if ingresos_total else None

    # Saldos de hoy
    saldos = saldos_al(cuentas_calc, movs, hoy_)
    activos, pasivos = activos_y_pasivos(cuentas_calc, movs, hoy_)
    filas_cuentas = [{"cuenta": c, "saldo": saldos[c.id]} for c in cuentas if c.activa]

    recientes = list(
        bd.scalars(
            select(Movimiento)
            .options(joinedload(Movimiento.categoria), joinedload(Movimiento.cuenta), joinedload(Movimiento.cuenta_destino))
            .order_by(Movimiento.fecha.desc(), Movimiento.id.desc())
            .limit(5)
        ).unique()
    )

    a_sig, m_sig = sumar_meses(anio, num_mes, 1)
    return render(
        request,
        "inicio.html",
        {
            "seccion": "inicio",
            "titulo_mes": nombre_mes(anio, num_mes),
            "es_mes_actual": es_mes_actual,
            "hoy": hoy_,
            "mes_anterior": f"{anio_ant:04d}-{mes_ant:02d}",
            "mes_siguiente": f"{a_sig:04d}-{m_sig:02d}",
            "mes_valor": f"{anio:04d}-{num_mes:02d}",
            "resumen": resumen,
            "anterior": anterior,
            "var_gastos": variacion(resumen.gastos, anterior.gastos),
            "var_ingresos": variacion(resumen.ingresos, anterior.ingresos),
            "por_categoria": por_categoria,
            "mayor_categoria": mayor_categoria,
            "graficos": graficos,
            "tabla_meses": tabla_meses,
            "referencia": referencia,
            "tasa_promedio": tasa_promedio,
            "patrimonio": activos - pasivos,
            "activos": activos,
            "pasivos": pasivos,
            "filas_cuentas": filas_cuentas,
            "recientes": recientes,
            "sin_cuentas": not cuentas,
        },
    )


@router.get("/mas")
def mas(request: Request):
    return render(request, "mas.html", {"seccion": "mas", "entorno": ajustes.entorno, "usa_2fa": bool(ajustes.totp_secret)})
