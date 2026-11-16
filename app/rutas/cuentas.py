"""Cuentas: efectivo, bancos, tarjetas, inversiones y préstamos."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..db import obtener_bd
from ..finanzas.calculos import CERO, saldos_al
from ..finanzas.servicio import cuentas_para_calculo, listar_cuentas, movs_para_calculo
from ..models import TIPOS_CUENTA, TIPOS_PASIVO, Cuenta, Movimiento
from ..seguridad import requiere_login, verificar_csrf
from ..tiempo import hoy
from ..web import avisar, render
from . import formularios as f

router = APIRouter(prefix="/cuentas", dependencies=[Depends(requiere_login)])


def filas_cuentas(bd: Session) -> dict:
    """Cuentas con su saldo de hoy, separadas en activos y pasivos."""
    hoy_ = hoy()
    cuentas = listar_cuentas(bd)
    movs = movs_para_calculo(bd, hasta=hoy_)
    saldos = saldos_al(cuentas_para_calculo(cuentas), movs, hoy_)
    activos, pasivos, inactivas = [], [], []
    for c in cuentas:
        saldo = saldos[c.id]
        fila = {"cuenta": c, "saldo": saldo}
        if c.es_pasivo:
            fila["deuda"] = -saldo
            fila["disponible"] = (c.cupo + saldo) if c.cupo is not None else None
        if not c.activa:
            inactivas.append(fila)
        elif c.es_pasivo:
            pasivos.append(fila)
        else:
            activos.append(fila)
    total_activos = sum((x["saldo"] for x in activos), CERO)
    total_pasivos = sum((x["deuda"] for x in pasivos), CERO)
    # Las cuentas inactivas también cuentan para el patrimonio si tienen saldo
    patrimonio = sum(saldos.values(), CERO)
    return {
        "activos": activos,
        "pasivos": pasivos,
        "inactivas": inactivas,
        "total_activos": total_activos,
        "total_pasivos": total_pasivos,
        "patrimonio": patrimonio,
    }


@router.get("")
def listado(request: Request, bd: Session = Depends(obtener_bd)):
    return render(request, "cuentas/lista.html", {"seccion": "cuentas", **filas_cuentas(bd)})


def _contexto(valores: dict, errores: dict | None = None, cuenta: Cuenta | None = None) -> dict:
    return {
        "seccion": "cuentas",
        "valores": valores,
        "errores": errores or {},
        "cuenta": cuenta,
        "tipos": TIPOS_CUENTA,
        "tipos_pasivo": sorted(TIPOS_PASIVO),
    }


def _leer(valores: dict) -> tuple[dict, dict]:
    errores = f.Errores()
    datos: dict = {}
    datos["nombre"] = f.texto(valores, "nombre", 80, errores, obligatorio=True)
    tipo = str(valores.get("tipo") or "")
    if tipo not in TIPOS_CUENTA:
        errores["tipo"] = "Elige un tipo de cuenta."
    datos["tipo"] = tipo
    datos["institucion"] = f.texto(valores, "institucion", 80, errores)
    es_pasivo = tipo in TIPOS_PASIVO
    saldo = f.monto(valores, "saldo_inicial", errores, obligatorio=False, positivo=False, permitir_negativo=not es_pasivo)
    datos["saldo_inicial"] = saldo if saldo is not None else CERO
    datos["fecha_saldo_inicial"] = f.fecha(valores, "fecha_saldo_inicial", errores)
    if es_pasivo:
        datos["cupo"] = f.monto(valores, "cupo", errores, obligatorio=False, positivo=False)
        datos["dia_corte"] = f.entero(valores, "dia_corte", errores, 1, 31)
        datos["dia_pago"] = f.entero(valores, "dia_pago", errores, 1, 31)
    else:
        datos["cupo"] = datos["dia_corte"] = datos["dia_pago"] = None
    datos["activa"] = f.marcado(valores, "activa")
    return datos, errores


@router.get("/nueva")
def nueva(request: Request):
    valores = {"tipo": "ahorros", "fecha_saldo_inicial": hoy().isoformat(), "activa": "1"}
    return render(request, "cuentas/form.html", _contexto(valores))


@router.post("/nueva", dependencies=[Depends(verificar_csrf)])
async def crear(request: Request, bd: Session = Depends(obtener_bd)):
    valores = dict(await request.form())
    datos, errores = _leer(valores)
    if errores:
        return render(request, "cuentas/form.html", _contexto(valores, errores), status_code=422)
    orden = (bd.scalar(select(func.max(Cuenta.orden))) or 0) + 1
    bd.add(Cuenta(**datos, orden=orden))
    bd.commit()
    avisar(request, f"Cuenta «{datos['nombre']}» creada.")
    return RedirectResponse("/cuentas", status_code=303)


def _obtener(bd: Session, cuenta_id: int) -> Cuenta:
    cuenta = bd.get(Cuenta, cuenta_id)
    if cuenta is None:
        raise HTTPException(status_code=404, detail="Esa cuenta no existe.")
    return cuenta


@router.get("/{cuenta_id}/editar")
def editar(request: Request, cuenta_id: int, bd: Session = Depends(obtener_bd)):
    c = _obtener(bd, cuenta_id)
    valores = {
        "nombre": c.nombre,
        "tipo": c.tipo,
        "institucion": c.institucion or "",
        "saldo_inicial": str(c.saldo_inicial),
        "fecha_saldo_inicial": c.fecha_saldo_inicial.isoformat(),
        "cupo": "" if c.cupo is None else str(c.cupo),
        "dia_corte": c.dia_corte or "",
        "dia_pago": c.dia_pago or "",
        "activa": "1" if c.activa else "",
    }
    usos = _usos(bd, c.id)
    return render(request, "cuentas/form.html", {**_contexto(valores, cuenta=c), "usos": usos})


def _usos(bd: Session, cuenta_id: int) -> int:
    return bd.scalar(
        select(func.count())
        .select_from(Movimiento)
        .where(or_(Movimiento.cuenta_id == cuenta_id, Movimiento.cuenta_destino_id == cuenta_id))
    ) or 0


@router.post("/{cuenta_id}/editar", dependencies=[Depends(verificar_csrf)])
async def actualizar(request: Request, cuenta_id: int, bd: Session = Depends(obtener_bd)):
    c = _obtener(bd, cuenta_id)
    valores = dict(await request.form())
    datos, errores = _leer(valores)
    usos = _usos(bd, c.id)
    if usos and datos.get("tipo") and (datos["tipo"] in TIPOS_PASIVO) != c.es_pasivo:
        errores["tipo"] = "Esta cuenta ya tiene movimientos: no puede pasar de activo a pasivo ni al revés."
    if errores:
        return render(
            request, "cuentas/form.html", {**_contexto(valores, errores, c), "usos": usos}, status_code=422
        )
    for campo, valor in datos.items():
        setattr(c, campo, valor)
    bd.commit()
    avisar(request, "Cuenta actualizada.")
    return RedirectResponse("/cuentas", status_code=303)


@router.post("/{cuenta_id}/eliminar", dependencies=[Depends(verificar_csrf)])
def eliminar(request: Request, cuenta_id: int, bd: Session = Depends(obtener_bd)):
    c = _obtener(bd, cuenta_id)
    usos = _usos(bd, c.id)
    if usos:
        avisar(request, f"«{c.nombre}» tiene {usos} movimientos. Desactívala en lugar de eliminarla.", "error")
        return RedirectResponse(f"/cuentas/{c.id}/editar", status_code=303)
    bd.delete(c)
    bd.commit()
    avisar(request, "Cuenta eliminada.")
    return RedirectResponse("/cuentas", status_code=303)
