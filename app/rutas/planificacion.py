"""Fase 1b: presupuestos, metas de ahorro, deudas y suscripciones."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from ..db import obtener_bd
from ..finanzas.calculos import CERO
from ..finanzas.montos import formato_usd
from ..finanzas.planificacion import (
    costo_mensual,
    estado_presupuestos,
    gastado_por_categoria_raiz,
    promedio_mensual,
    proximos_pagos,
    siguiente_fecha,
)
from ..finanzas.servicio import (
    asegurar_categorias_base,
    listar_categorias,
    listar_cuentas,
    movs_para_calculo,
    padre_de,
    presupuestos_datos,
    progreso_metas,
)
from ..models import (
    FRECUENCIAS,
    TIPOS_PASIVO,
    TIPOS_RECURRENTE,
    Categoria,
    Cuenta,
    MetaAhorro,
    Movimiento,
    Presupuesto,
    Recurrente,
)
from ..seguridad import requiere_login, verificar_csrf
from ..tiempo import hoy, nombre_mes, parse_mes, primer_dia, sumar_meses, ultimo_dia
from ..web import avisar, render
from . import formularios as f

presupuestos = APIRouter(prefix="/presupuestos", dependencies=[Depends(requiere_login)])
metas = APIRouter(prefix="/metas", dependencies=[Depends(requiere_login)])
recurrentes = APIRouter(prefix="/recurrentes", dependencies=[Depends(requiere_login)])


def _mes_desde_texto(texto: str | None, errores: dict, campo: str) -> date | None:
    try:
        anio, mes = (int(x) for x in str(texto or "").split("-")[:2])
        return primer_dia(anio, mes)
    except (ValueError, TypeError):
        errores[campo] = "Elige un mes."
        return None


# =============================================================================
# Presupuestos
# =============================================================================


@presupuestos.get("")
def lista_presupuestos(request: Request, mes: str | None = None, bd: Session = Depends(obtener_bd)):
    asegurar_categorias_base(bd)
    hoy_ = hoy()
    anio, num = parse_mes(mes, hoy_)
    movs = movs_para_calculo(bd)
    padres = padre_de(bd)
    categorias = {c.id: c for c in listar_categorias(bd)}
    estados = estado_presupuestos(presupuestos_datos(bd), movs, anio, num, padres, hoy_)
    con_presupuesto = {e.categoria_id for e in estados}
    gastado = gastado_por_categoria_raiz(movs, primer_dia(anio, num), ultimo_dia(anio, num), padres)
    sin_presupuesto = sorted(
        ((categorias.get(cid), total) for cid, total in gastado.items() if cid not in con_presupuesto and cid in categorias),
        key=lambda par: par[1], reverse=True,
    )
    a_ant, m_ant = sumar_meses(anio, num, -1)
    a_sig, m_sig = sumar_meses(anio, num, 1)
    return render(request, "planificacion/presupuestos.html", {
        "seccion": "mas",
        "estados": [(e, categorias.get(e.categoria_id)) for e in estados],
        "sin_presupuesto": sin_presupuesto,
        "total_presupuesto": sum((e.monto for e in estados), CERO),
        "total_gastado": sum((e.gastado for e in estados), CERO),
        "titulo_mes": nombre_mes(anio, num),
        "mes_anterior": f"{a_ant:04d}-{m_ant:02d}",
        "mes_siguiente": f"{a_sig:04d}-{m_sig:02d}",
        "es_mes_actual": (anio, num) == (hoy_.year, hoy_.month),
    })


def _contexto_presupuesto(bd: Session, valores: dict, errores: dict | None = None, presupuesto=None) -> dict:
    principales = [c for c in listar_categorias(bd, "gasto", solo_activas=True) if c.padre_id is None]
    movs, padres = movs_para_calculo(bd), padre_de(bd)
    sugerencias = {c.id: promedio_mensual(movs, c.id, padres, hoy()) for c in principales}
    return {"seccion": "mas", "valores": valores, "errores": errores or {}, "presupuesto": presupuesto,
            "categorias": principales, "sugerencias": sugerencias}


def _leer_presupuesto(bd: Session, valores: dict, actual: Presupuesto | None) -> tuple[dict, dict]:
    errores = f.Errores()
    datos: dict = {}
    crudo = str(valores.get("categoria_id") or "")
    categoria = bd.get(Categoria, int(crudo)) if crudo.isdigit() else None
    if categoria is None or categoria.tipo != "gasto" or categoria.padre_id is not None:
        errores["categoria_id"] = "Elige una categoría principal de gasto."
    else:
        datos["categoria_id"] = categoria.id
    datos["monto"] = f.monto(valores, "monto", errores)
    datos["desde_mes"] = _mes_desde_texto(valores.get("desde_mes"), errores, "desde_mes")
    if not errores:
        solapado = bd.scalar(
            select(Presupuesto.id).where(
                Presupuesto.categoria_id == datos["categoria_id"],
                Presupuesto.id != (actual.id if actual else -1),
                (Presupuesto.hasta_mes.is_(None)) | (Presupuesto.hasta_mes >= datos["desde_mes"]),
            )
        )
        if solapado:
            errores["categoria_id"] = "Esa categoría ya tiene un presupuesto vigente. Edítalo en lugar de crear otro."
    return datos, errores


@presupuestos.get("/nuevo")
def nuevo_presupuesto(request: Request, categoria: str = "", bd: Session = Depends(obtener_bd)):
    hoy_ = hoy()
    valores = {"categoria_id": categoria, "desde_mes": f"{hoy_:%Y-%m}"}
    return render(request, "planificacion/presupuesto_form.html", _contexto_presupuesto(bd, valores))


@presupuestos.post("/nuevo", dependencies=[Depends(verificar_csrf)])
async def crear_presupuesto(request: Request, bd: Session = Depends(obtener_bd)):
    valores = dict(await request.form())
    datos, errores = _leer_presupuesto(bd, valores, None)
    if errores:
        return render(request, "planificacion/presupuesto_form.html", _contexto_presupuesto(bd, valores, errores), status_code=422)
    bd.add(Presupuesto(**datos))
    bd.commit()
    avisar(request, "Presupuesto creado.")
    return RedirectResponse("/presupuestos", status_code=303)


def _obtener_presupuesto(bd: Session, pid: int) -> Presupuesto:
    p = bd.get(Presupuesto, pid)
    if p is None:
        raise HTTPException(404, "Ese presupuesto no existe.")
    return p


@presupuestos.get("/{pid}/editar")
def editar_presupuesto(request: Request, pid: int, bd: Session = Depends(obtener_bd)):
    p = _obtener_presupuesto(bd, pid)
    valores = {"categoria_id": str(p.categoria_id), "monto": str(p.monto), "desde_mes": f"{p.desde_mes:%Y-%m}"}
    return render(request, "planificacion/presupuesto_form.html", _contexto_presupuesto(bd, valores, presupuesto=p))


@presupuestos.post("/{pid}/editar", dependencies=[Depends(verificar_csrf)])
async def actualizar_presupuesto(request: Request, pid: int, bd: Session = Depends(obtener_bd)):
    p = _obtener_presupuesto(bd, pid)
    valores = dict(await request.form())
    datos, errores = _leer_presupuesto(bd, valores, p)
    if errores:
        return render(request, "planificacion/presupuesto_form.html", _contexto_presupuesto(bd, valores, errores, p), status_code=422)
    inicio_mes = primer_dia(hoy().year, hoy().month)
    if p.desde_mes < inicio_mes and datos["monto"] != p.monto and datos["desde_mes"] == p.desde_mes:
        # Se conserva la historia: el monto viejo vale hasta el mes pasado y el nuevo desde este mes
        a, m = sumar_meses(inicio_mes.year, inicio_mes.month, -1)
        p.hasta_mes = primer_dia(a, m)
        bd.add(Presupuesto(categoria_id=p.categoria_id, monto=datos["monto"], desde_mes=inicio_mes))
    else:
        for campo, valor in datos.items():
            setattr(p, campo, valor)
    bd.commit()
    avisar(request, "Presupuesto actualizado.")
    return RedirectResponse("/presupuestos", status_code=303)


@presupuestos.post("/{pid}/eliminar", dependencies=[Depends(verificar_csrf)])
def eliminar_presupuesto(request: Request, pid: int, bd: Session = Depends(obtener_bd)):
    p = _obtener_presupuesto(bd, pid)
    inicio_mes = primer_dia(hoy().year, hoy().month)
    if p.desde_mes < inicio_mes:
        a, m = sumar_meses(inicio_mes.year, inicio_mes.month, -1)
        p.hasta_mes = primer_dia(a, m)  # termina el mes pasado; los meses anteriores conservan su presupuesto
    else:
        bd.delete(p)
    bd.commit()
    avisar(request, "Presupuesto quitado desde este mes.")
    return RedirectResponse("/presupuestos", status_code=303)


# =============================================================================
# Metas de ahorro
# =============================================================================


@metas.get("")
def lista_metas(request: Request, bd: Session = Depends(obtener_bd)):
    todas = progreso_metas(bd, hoy(), solo_activas=False)
    return render(request, "planificacion/metas.html", {
        "seccion": "mas",
        "activas": [(m, p) for m, p in todas if m.estado == "activa"],
        "archivadas": [(m, p) for m, p in todas if m.estado != "activa"],
    })


def _leer_meta(valores: dict) -> tuple[dict, dict]:
    errores = f.Errores()
    datos = {
        "nombre": f.texto(valores, "nombre", 80, errores, obligatorio=True),
        "icono": f.texto(valores, "icono", 16, errores),
        "monto_objetivo": f.monto(valores, "monto_objetivo", errores),
        "fecha_objetivo": f.fecha(valores, "fecha_objetivo", errores, obligatorio=False),
    }
    saldo = f.monto(valores, "saldo_inicial", errores, obligatorio=False, positivo=False)
    datos["saldo_inicial"] = saldo if saldo is not None else CERO
    return datos, errores


@metas.get("/nueva")
def nueva_meta(request: Request):
    return render(request, "planificacion/meta_form.html", {"seccion": "mas", "valores": {}, "errores": {}, "meta": None})


@metas.post("/nueva", dependencies=[Depends(verificar_csrf)])
async def crear_meta(request: Request, bd: Session = Depends(obtener_bd)):
    valores = dict(await request.form())
    datos, errores = _leer_meta(valores)
    if errores:
        return render(request, "planificacion/meta_form.html",
                      {"seccion": "mas", "valores": valores, "errores": errores, "meta": None}, status_code=422)
    orden = (bd.scalar(select(func.max(MetaAhorro.orden))) or 0) + 1
    bd.add(MetaAhorro(**datos, orden=orden))
    bd.commit()
    avisar(request, f"Meta «{datos['nombre']}» creada. Para aportar, registra un Ahorro y elige la meta.")
    return RedirectResponse("/metas", status_code=303)


def _obtener_meta(bd: Session, mid: int) -> MetaAhorro:
    m = bd.get(MetaAhorro, mid)
    if m is None:
        raise HTTPException(404, "Esa meta no existe.")
    return m


@metas.get("/{mid}/editar")
def editar_meta(request: Request, mid: int, bd: Session = Depends(obtener_bd)):
    m = _obtener_meta(bd, mid)
    valores = {"nombre": m.nombre, "icono": m.icono or "", "monto_objetivo": str(m.monto_objetivo),
               "fecha_objetivo": m.fecha_objetivo.isoformat() if m.fecha_objetivo else "",
               "saldo_inicial": str(m.saldo_inicial)}
    return render(request, "planificacion/meta_form.html", {"seccion": "mas", "valores": valores, "errores": {}, "meta": m})


@metas.post("/{mid}/editar", dependencies=[Depends(verificar_csrf)])
async def actualizar_meta(request: Request, mid: int, bd: Session = Depends(obtener_bd)):
    m = _obtener_meta(bd, mid)
    valores = dict(await request.form())
    datos, errores = _leer_meta(valores)
    if errores:
        return render(request, "planificacion/meta_form.html",
                      {"seccion": "mas", "valores": valores, "errores": errores, "meta": m}, status_code=422)
    for campo, valor in datos.items():
        setattr(m, campo, valor)
    bd.commit()
    avisar(request, "Meta actualizada.")
    return RedirectResponse("/metas", status_code=303)


@metas.post("/{mid}/archivar", dependencies=[Depends(verificar_csrf)])
def archivar_meta(request: Request, mid: int, bd: Session = Depends(obtener_bd)):
    m = _obtener_meta(bd, mid)
    m.estado = "archivada" if m.estado == "activa" else "activa"
    bd.commit()
    avisar(request, "Meta archivada." if m.estado == "archivada" else "Meta reactivada.")
    return RedirectResponse("/metas", status_code=303)


# =============================================================================
# Deudas y suscripciones (pagos recurrentes)
# =============================================================================


@recurrentes.get("")
def lista_recurrentes(request: Request, bd: Session = Depends(obtener_bd)):
    hoy_ = hoy()
    todos = list(bd.scalars(
        select(Recurrente).options(joinedload(Recurrente.cuenta), joinedload(Recurrente.cuenta_destino),
                                   joinedload(Recurrente.categoria))
        .order_by(Recurrente.activa.desc(), Recurrente.proxima_fecha)
    ).unique())
    activos = [r for r in todos if r.activa]
    por_id = {r.id: r for r in activos}
    proximos = [(p, por_id[p.recurrente_id]) for p in proximos_pagos(
        ((r.id, r.proxima_fecha, r.monto) for r in activos), hoy_, 30)]
    costo = {r.id: costo_mensual(r.monto, r.frecuencia) for r in activos}
    revisar_uso = [r for r in activos if r.tipo == "suscripcion"
                   and (r.revision_uso is None or (r.revision_uso.year, r.revision_uso.month) != (hoy_.year, hoy_.month))]
    return render(request, "planificacion/recurrentes.html", {
        "seccion": "mas",
        "proximos": proximos,
        "activos": activos,
        "inactivos": [r for r in todos if not r.activa],
        "costo": costo,
        "total_mensual": sum(costo.values(), CERO),
        "total_suscripciones": sum((costo[r.id] for r in activos if r.tipo == "suscripcion"), CERO),
        "revisar_uso": revisar_uso,
        "tipos": TIPOS_RECURRENTE,
        "frecuencias": FRECUENCIAS,
        "hoy": hoy_,
    })


def _contexto_recurrente(bd: Session, valores: dict, errores: dict | None = None, rec=None) -> dict:
    cuentas = listar_cuentas(bd, solo_activas=True)
    return {"seccion": "mas", "valores": valores, "errores": errores or {}, "recurrente": rec,
            "cuentas": cuentas, "pasivos": [c for c in cuentas if c.tipo in TIPOS_PASIVO],
            "categorias": listar_categorias(bd, "gasto", solo_activas=True),
            "tipos": TIPOS_RECURRENTE, "frecuencias": FRECUENCIAS}


def _leer_recurrente(bd: Session, valores: dict) -> tuple[dict, dict]:
    errores = f.Errores()
    datos: dict = {"nombre": f.texto(valores, "nombre", 80, errores, obligatorio=True)}
    tipo = str(valores.get("tipo") or "")
    if tipo not in TIPOS_RECURRENTE:
        errores["tipo"] = "Elige el tipo."
    datos["tipo"] = tipo
    datos["monto"] = f.monto(valores, "monto", errores)
    frecuencia = str(valores.get("frecuencia") or "")
    if frecuencia not in FRECUENCIAS:
        errores["frecuencia"] = "Elige la frecuencia."
    datos["frecuencia"] = frecuencia
    datos["proxima_fecha"] = f.fecha(valores, "proxima_fecha", errores)
    if datos["proxima_fecha"]:
        datos["dia"] = datos["proxima_fecha"].day
    crudo = str(valores.get("cuenta_id") or "")
    cuenta = bd.get(Cuenta, int(crudo)) if crudo.isdigit() else None
    if cuenta is None:
        errores["cuenta_id"] = "Elige la cuenta con la que pagas."
    datos["cuenta_id"] = cuenta.id if cuenta else None
    datos["cuenta_destino_id"] = None
    datos["categoria_id"] = None
    destino_txt = str(valores.get("cuenta_destino_id") or "")
    if destino_txt.isdigit():
        destino = bd.get(Cuenta, int(destino_txt))
        if destino is None or destino.tipo not in TIPOS_PASIVO:
            errores["cuenta_destino_id"] = "Debe ser una tarjeta o un préstamo."
        elif cuenta and destino.id == cuenta.id:
            errores["cuenta_destino_id"] = "Debe ser distinta de la cuenta de pago."
        else:
            datos["cuenta_destino_id"] = destino.id
    else:
        cat_txt = str(valores.get("categoria_id") or "")
        categoria = bd.get(Categoria, int(cat_txt)) if cat_txt.isdigit() else None
        if categoria is None or categoria.tipo != "gasto":
            errores["categoria_id"] = "Elige la categoría del gasto (o la deuda que pagas)."
        else:
            datos["categoria_id"] = categoria.id
    datos["dias_aviso"] = f.entero(valores, "dias_aviso", errores, 0, 30) or 0
    datos["nota"] = f.texto(valores, "nota", 500, errores)
    datos["activa"] = f.marcado(valores, "activa")
    return datos, errores


@recurrentes.get("/nuevo")
def nuevo_recurrente(request: Request, bd: Session = Depends(obtener_bd)):
    asegurar_categorias_base(bd)
    valores = {"tipo": "suscripcion", "frecuencia": "mensual", "dias_aviso": "3", "activa": "1",
               "proxima_fecha": hoy().isoformat()}
    return render(request, "planificacion/recurrente_form.html", _contexto_recurrente(bd, valores))


@recurrentes.post("/nuevo", dependencies=[Depends(verificar_csrf)])
async def crear_recurrente(request: Request, bd: Session = Depends(obtener_bd)):
    valores = dict(await request.form())
    datos, errores = _leer_recurrente(bd, valores)
    if errores:
        return render(request, "planificacion/recurrente_form.html", _contexto_recurrente(bd, valores, errores), status_code=422)
    bd.add(Recurrente(**datos))
    bd.commit()
    avisar(request, f"«{datos['nombre']}» agregado a tus pagos recurrentes.")
    return RedirectResponse("/recurrentes", status_code=303)


def _obtener_recurrente(bd: Session, rid: int) -> Recurrente:
    r = bd.get(Recurrente, rid)
    if r is None:
        raise HTTPException(404, "Ese pago recurrente no existe.")
    return r


@recurrentes.get("/{rid}/editar")
def editar_recurrente(request: Request, rid: int, bd: Session = Depends(obtener_bd)):
    r = _obtener_recurrente(bd, rid)
    valores = {"nombre": r.nombre, "tipo": r.tipo, "monto": str(r.monto), "frecuencia": r.frecuencia,
               "proxima_fecha": r.proxima_fecha.isoformat(), "cuenta_id": str(r.cuenta_id),
               "cuenta_destino_id": str(r.cuenta_destino_id or ""), "categoria_id": str(r.categoria_id or ""),
               "dias_aviso": str(r.dias_aviso), "nota": r.nota or "", "activa": "1" if r.activa else ""}
    return render(request, "planificacion/recurrente_form.html", _contexto_recurrente(bd, valores, rec=r))


@recurrentes.post("/{rid}/editar", dependencies=[Depends(verificar_csrf)])
async def actualizar_recurrente(request: Request, rid: int, bd: Session = Depends(obtener_bd)):
    r = _obtener_recurrente(bd, rid)
    valores = dict(await request.form())
    datos, errores = _leer_recurrente(bd, valores)
    if errores:
        return render(request, "planificacion/recurrente_form.html", _contexto_recurrente(bd, valores, errores, r), status_code=422)
    for campo, valor in datos.items():
        setattr(r, campo, valor)
    bd.commit()
    avisar(request, "Pago recurrente actualizado.")
    return RedirectResponse("/recurrentes", status_code=303)


@recurrentes.post("/{rid}/pagar", dependencies=[Depends(verificar_csrf)])
def pagar_recurrente(request: Request, rid: int, bd: Session = Depends(obtener_bd)):
    """Registra el pago con un toque: gasto (o transferencia a la deuda) y avanza el próximo vencimiento."""
    r = _obtener_recurrente(bd, rid)
    mov = Movimiento(
        fecha=hoy(),
        tipo="transferencia" if r.cuenta_destino_id else "gasto",
        monto=r.monto,
        cuenta_id=r.cuenta_id,
        cuenta_destino_id=r.cuenta_destino_id,
        categoria_id=None if r.cuenta_destino_id else r.categoria_id,
        comercio=r.nombre,
        origen="recurrente",
        recurrente_id=r.id,
    )
    bd.add(mov)
    pagado = r.proxima_fecha
    r.proxima_fecha = siguiente_fecha(r.proxima_fecha, r.frecuencia, r.dia)
    bd.commit()
    avisar(request, f"Pago de «{r.nombre}» ({formato_usd(r.monto)}) registrado por el vencimiento del "
                    f"{pagado:%d/%m}. Próximo: {r.proxima_fecha:%d/%m/%Y}.")
    destino = request.headers.get("referer") or "/recurrentes"
    return RedirectResponse(destino if destino.startswith(str(request.base_url)) else "/recurrentes", status_code=303)


@recurrentes.post("/{rid}/uso", dependencies=[Depends(verificar_csrf)])
async def registrar_uso(request: Request, rid: int, bd: Session = Depends(obtener_bd)):
    r = _obtener_recurrente(bd, rid)
    usada = str((await request.form()).get("usada")) == "1"
    r.meses_sin_uso = 0 if usada else r.meses_sin_uso + 1
    r.revision_uso = hoy()
    bd.commit()
    if not usada and r.meses_sin_uso >= 2:
        avisar(request, f"Llevas {r.meses_sin_uso} meses sin usar «{r.nombre}»: cancelarla te ahorra "
                        f"{formato_usd(costo_mensual(r.monto, r.frecuencia) * 12)} al año.", "alerta")
    else:
        avisar(request, "Anotado.")
    return RedirectResponse("/recurrentes", status_code=303)


@recurrentes.post("/{rid}/eliminar", dependencies=[Depends(verificar_csrf)])
def eliminar_recurrente(request: Request, rid: int, bd: Session = Depends(obtener_bd)):
    r = _obtener_recurrente(bd, rid)
    usado = bd.scalar(select(func.count()).select_from(Movimiento).where(Movimiento.recurrente_id == r.id))
    if usado:
        r.activa = False
        avisar(request, f"«{r.nombre}» tiene pagos registrados: lo desactivé en lugar de borrarlo.")
    else:
        bd.delete(r)
        avisar(request, "Pago recurrente eliminado.")
    bd.commit()
    return RedirectResponse("/recurrentes", status_code=303)
