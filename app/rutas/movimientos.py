"""Registro, edición y listado de movimientos (ingresos, gastos, transferencias y ahorros)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, joinedload

from ..db import obtener_bd
from ..finanzas.calculos import CERO
from ..finanzas.montos import formato_usd
from ..finanzas.servicio import (
    asegurar_categorias_base,
    categorias_frecuentes,
    cuenta_por_defecto,
    destino_ahorro_por_defecto,
    listar_categorias,
    listar_cuentas,
)
from ..models import RUBROS_SRI, TIPOS_AHORRO, Categoria, Cuenta, Movimiento
from ..seguridad import requiere_login, verificar_csrf
from ..tiempo import hoy, nombre_mes, parse_mes, primer_dia, sumar_meses, ultimo_dia
from ..web import avisar, es_htmx, render
from . import formularios as f

router = APIRouter(prefix="/movimientos", dependencies=[Depends(requiere_login)])

TIPOS_FORMULARIO = ("gasto", "ingreso", "transferencia", "ahorro")
TITULOS = {"gasto": "Gasto", "ingreso": "Ingreso", "transferencia": "Transferencia", "ahorro": "Ahorro"}


# --- Listado ----------------------------------------------------------------


@router.get("")
def listado(
    request: Request,
    mes: str | None = None,
    tipo: str = "",
    cuenta: str = "",
    categoria: str = "",
    q: str = "",
    bd: Session = Depends(obtener_bd),
):
    anio, num_mes = parse_mes(mes, hoy())
    desde, hasta = primer_dia(anio, num_mes), ultimo_dia(anio, num_mes)

    consulta = (
        select(Movimiento)
        .options(
            joinedload(Movimiento.cuenta),
            joinedload(Movimiento.cuenta_destino),
            joinedload(Movimiento.categoria),
        )
        .where(Movimiento.fecha >= desde, Movimiento.fecha <= hasta)
        .order_by(Movimiento.fecha.desc(), Movimiento.id.desc())
    )
    if tipo in ("gasto", "ingreso", "transferencia"):
        consulta = consulta.where(Movimiento.tipo == tipo)
    if cuenta.isdigit():
        consulta = consulta.where(
            or_(Movimiento.cuenta_id == int(cuenta), Movimiento.cuenta_destino_id == int(cuenta))
        )
    if categoria.isdigit():
        hijas = select(Categoria.id).where(Categoria.padre_id == int(categoria))
        consulta = consulta.where(
            or_(Movimiento.categoria_id == int(categoria), Movimiento.categoria_id.in_(hijas))
        )
    busqueda = q.strip()[:60]
    if busqueda:
        patron = f"%{busqueda}%"
        consulta = consulta.where(or_(Movimiento.comercio.ilike(patron), Movimiento.nota.ilike(patron)))

    movimientos = list(bd.scalars(consulta).unique())

    dias: list[dict] = []
    for mov in movimientos:
        if not dias or dias[-1]["fecha"] != mov.fecha:
            dias.append({"fecha": mov.fecha, "movimientos": [], "neto": CERO})
        dias[-1]["movimientos"].append(mov)
        if mov.tipo == "ingreso":
            dias[-1]["neto"] += mov.monto
        elif mov.tipo == "gasto":
            dias[-1]["neto"] -= mov.monto

    ingresos = sum((m.monto for m in movimientos if m.tipo == "ingreso"), CERO)
    gastos = sum((m.monto for m in movimientos if m.tipo == "gasto"), CERO)

    filtros = {"tipo": tipo, "cuenta": cuenta, "categoria": categoria, "q": busqueda}
    filtros_activos = {k: v for k, v in filtros.items() if v}

    def enlace_mes(delta: int) -> str:
        a, m = sumar_meses(anio, num_mes, delta)
        return "/movimientos?" + urlencode({"mes": f"{a:04d}-{m:02d}", **filtros_activos})

    contexto = {
        "seccion": "movimientos",
        "dias": dias,
        "total_movimientos": len(movimientos),
        "ingresos": ingresos,
        "gastos": gastos,
        "titulo_mes": nombre_mes(anio, num_mes),
        "mes_valor": f"{anio:04d}-{num_mes:02d}",
        "mes_anterior": enlace_mes(-1),
        "mes_siguiente": enlace_mes(1),
        "filtros": filtros,
        "hay_filtros": bool(filtros_activos),
        "cuentas": listar_cuentas(bd),
        "categorias": listar_categorias(bd),
    }
    plantilla = "movimientos/_lista.html" if es_htmx(request) else "movimientos/lista.html"
    return render(request, plantilla, contexto)


# --- Formulario -------------------------------------------------------------


def _contexto_formulario(
    request: Request,
    bd: Session,
    valores: dict,
    errores: dict | None = None,
    movimiento: Movimiento | None = None,
) -> dict:
    hoy_ = hoy()
    cuentas = listar_cuentas(bd)
    # Al editar, la cuenta actual se muestra aunque esté desactivada
    ids_actuales = {movimiento.cuenta_id, movimiento.cuenta_destino_id} if movimiento else set()
    visibles = [c for c in cuentas if c.activa or c.id in ids_actuales]
    categorias_gasto = [c for c in listar_categorias(bd, "gasto") if c.activa or (movimiento and c.id == movimiento.categoria_id)]
    categorias_ingreso = [c for c in listar_categorias(bd, "ingreso") if c.activa or (movimiento and c.id == movimiento.categoria_id)]
    return {
        "seccion": "agregar",
        "valores": valores,
        "errores": errores or {},
        "movimiento": movimiento,
        "titulos": TITULOS,
        "cuentas": visibles,
        "cuentas_ahorro": [c for c in visibles if c.tipo in TIPOS_AHORRO],
        "categorias_gasto": categorias_gasto,
        "categorias_ingreso": categorias_ingreso,
        "frecuentes_gasto": categorias_frecuentes(bd, "gasto", hoy_),
        "frecuentes_ingreso": categorias_frecuentes(bd, "ingreso", hoy_),
        "rubros": RUBROS_SRI,
        "hoy": hoy_,
        "ayer": hoy_ - timedelta(days=1),
    }


def _valores_iniciales(bd: Session, tipo: str) -> dict:
    return {
        "tipo": tipo,
        "fecha": hoy().isoformat(),
        "cuenta_id": str(cuenta_por_defecto(bd, tipo) or ""),
        "cuenta_destino_ahorro": str(destino_ahorro_por_defecto(bd) or ""),
    }


def _valores_de_movimiento(mov: Movimiento) -> dict:
    def txt(v):
        return "" if v is None else str(v)

    return {
        "tipo": mov.tipo,
        "monto": txt(mov.monto),
        "fecha": mov.fecha.isoformat(),
        "cuenta_id": txt(mov.cuenta_id),
        "cuenta_destino_id": txt(mov.cuenta_destino_id),
        "categoria_gasto": txt(mov.categoria_id) if mov.tipo == "gasto" else "",
        "categoria_ingreso": txt(mov.categoria_id) if mov.tipo == "ingreso" else "",
        "comercio": txt(mov.comercio),
        "nota": txt(mov.nota),
        "subtotal": txt(mov.subtotal),
        "iva": txt(mov.iva),
        "ruc_emisor": txt(mov.ruc_emisor),
        "clave_acceso": txt(mov.clave_acceso),
        "deducible": "1" if mov.deducible else "",
    }


def _leer_y_validar(bd: Session, valores: dict, movimiento: Movimiento | None) -> tuple[dict, dict]:
    """Devuelve (datos listos para guardar, errores)."""
    errores = f.Errores()
    tipo = valores.get("tipo")
    if tipo not in TIPOS_FORMULARIO:
        errores["tipo"] = "Elige el tipo de movimiento."
        return {}, errores

    datos: dict = {"tipo": "transferencia" if tipo in ("transferencia", "ahorro") else tipo}
    datos["monto"] = f.monto(valores, "monto", errores)
    datos["fecha"] = f.fecha(valores, "fecha", errores)
    if datos["fecha"] and datos["fecha"] > hoy() + timedelta(days=366):
        errores["fecha"] = "La fecha está a más de un año en el futuro."

    ids_actuales = {movimiento.cuenta_id, movimiento.cuenta_destino_id} if movimiento else set()

    def cuenta_valida(campo: str) -> Cuenta | None:
        crudo = str(valores.get(campo) or "")
        cuenta = bd.get(Cuenta, int(crudo)) if crudo.isdigit() else None
        if cuenta is None or not (cuenta.activa or cuenta.id in ids_actuales):
            errores[campo] = "Elige una cuenta."
            return None
        return cuenta

    origen = cuenta_valida("cuenta_id")
    datos["cuenta_id"] = origen.id if origen else None
    datos["cuenta_destino_id"] = None
    datos["categoria_id"] = None

    if tipo in ("gasto", "ingreso"):
        campo = f"categoria_{tipo}"
        crudo = str(valores.get(campo) or "")
        categoria = bd.get(Categoria, int(crudo)) if crudo.isdigit() else None
        if categoria is None or categoria.tipo != tipo:
            errores[campo] = "Elige una categoría."
        else:
            datos["categoria_id"] = categoria.id
    else:
        campo_destino = "cuenta_destino_ahorro" if tipo == "ahorro" else "cuenta_destino_id"
        destino = cuenta_valida(campo_destino)
        if destino and origen and destino.id == origen.id:
            errores[campo_destino] = "La cuenta de destino debe ser distinta de la de origen."
        elif destino and tipo == "ahorro" and destino.tipo not in TIPOS_AHORRO:
            errores[campo_destino] = "Para un ahorro, elige una cuenta de ahorros o inversión."
        datos["cuenta_destino_id"] = destino.id if destino else None

    datos["comercio"] = f.texto(valores, "comercio", 120, errores)
    datos["nota"] = f.texto(valores, "nota", 1000, errores)
    datos["subtotal"] = f.monto(valores, "subtotal", errores, obligatorio=False, positivo=False)
    datos["iva"] = f.monto(valores, "iva", errores, obligatorio=False, positivo=False)
    datos["ruc_emisor"] = f.solo_digitos(valores, "ruc_emisor", 13, errores)
    datos["clave_acceso"] = f.solo_digitos(valores, "clave_acceso", 49, errores)
    datos["deducible"] = tipo == "gasto" and f.marcado(valores, "deducible")

    if datos["clave_acceso"]:
        duplicado = bd.scalar(
            select(Movimiento.id).where(
                Movimiento.clave_acceso == datos["clave_acceso"],
                Movimiento.id != (movimiento.id if movimiento else -1),
            )
        )
        if duplicado:
            errores["clave_acceso"] = "Esa factura ya está registrada."
    return datos, errores


def _redirigir_tras_guardar(mov: Movimiento) -> RedirectResponse:
    return RedirectResponse(f"/movimientos?mes={mov.fecha:%Y-%m}", status_code=303)


@router.get("/nuevo")
def nuevo(request: Request, tipo: str = "gasto", bd: Session = Depends(obtener_bd)):
    asegurar_categorias_base(bd)
    tipo = tipo if tipo in TIPOS_FORMULARIO else "gasto"
    return render(request, "movimientos/form.html", _contexto_formulario(request, bd, _valores_iniciales(bd, tipo)))


@router.post("/nuevo", dependencies=[Depends(verificar_csrf)])
async def crear(request: Request, bd: Session = Depends(obtener_bd)):
    valores = dict(await request.form())
    datos, errores = _leer_y_validar(bd, valores, None)
    if errores:
        return render(
            request, "movimientos/form.html", _contexto_formulario(request, bd, valores, errores), status_code=422
        )
    mov = Movimiento(**datos, origen="manual")
    bd.add(mov)
    bd.commit()
    titulo = TITULOS[valores["tipo"]]
    avisar(request, f"{titulo} de {formato_usd(mov.monto)} guardado.")
    if valores.get("otro") == "1":
        return RedirectResponse(f"/movimientos/nuevo?tipo={valores['tipo']}", status_code=303)
    return _redirigir_tras_guardar(mov)


def _obtener(bd: Session, movimiento_id: int) -> Movimiento:
    mov = bd.get(Movimiento, movimiento_id)
    if mov is None:
        raise HTTPException(status_code=404, detail="Ese movimiento no existe.")
    return mov


@router.get("/{movimiento_id}/editar")
def editar(request: Request, movimiento_id: int, bd: Session = Depends(obtener_bd)):
    mov = _obtener(bd, movimiento_id)
    valores = _valores_de_movimiento(mov)
    return render(request, "movimientos/form.html", _contexto_formulario(request, bd, valores, movimiento=mov))


@router.post("/{movimiento_id}/editar", dependencies=[Depends(verificar_csrf)])
async def actualizar(request: Request, movimiento_id: int, bd: Session = Depends(obtener_bd)):
    mov = _obtener(bd, movimiento_id)
    valores = dict(await request.form())
    datos, errores = _leer_y_validar(bd, valores, mov)
    if errores:
        return render(
            request,
            "movimientos/form.html",
            _contexto_formulario(request, bd, valores, errores, movimiento=mov),
            status_code=422,
        )
    for campo, valor in datos.items():
        setattr(mov, campo, valor)
    bd.commit()
    avisar(request, "Movimiento actualizado.")
    return _redirigir_tras_guardar(mov)


@router.post("/{movimiento_id}/eliminar", dependencies=[Depends(verificar_csrf)])
def eliminar(request: Request, movimiento_id: int, bd: Session = Depends(obtener_bd)):
    mov = _obtener(bd, movimiento_id)
    mes = f"{mov.fecha:%Y-%m}"
    bd.delete(mov)
    bd.commit()
    avisar(request, "Movimiento eliminado.")
    return RedirectResponse(f"/movimientos?mes={mes}", status_code=303)
