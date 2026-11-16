"""Categorías de ingresos y gastos, con su rubro de gastos personales del SRI."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import obtener_bd
from ..finanzas.servicio import asegurar_categorias_base, listar_categorias
from ..models import RUBROS_SRI, Categoria, Movimiento
from ..seguridad import requiere_login, verificar_csrf
from ..web import avisar, render
from . import formularios as f

router = APIRouter(prefix="/categorias", dependencies=[Depends(requiere_login)])


def _usos(bd: Session) -> dict[int, int]:
    filas = bd.execute(
        select(Movimiento.categoria_id, func.count()).where(Movimiento.categoria_id.is_not(None)).group_by(Movimiento.categoria_id)
    ).all()
    return {cat_id: veces for cat_id, veces in filas}


@router.get("")
def listado(request: Request, bd: Session = Depends(obtener_bd)):
    asegurar_categorias_base(bd)
    categorias = listar_categorias(bd)
    return render(
        request,
        "categorias/lista.html",
        {
            "seccion": "mas",
            "gasto": [c for c in categorias if c.tipo == "gasto"],
            "ingreso": [c for c in categorias if c.tipo == "ingreso"],
            "usos": _usos(bd),
            "rubros": RUBROS_SRI,
        },
    )


def _contexto(bd: Session, valores: dict, errores: dict | None = None, categoria: Categoria | None = None) -> dict:
    posibles_padres = [
        c for c in listar_categorias(bd, solo_activas=True) if c.padre_id is None and (categoria is None or c.id != categoria.id)
    ]
    return {
        "seccion": "mas",
        "valores": valores,
        "errores": errores or {},
        "categoria": categoria,
        "rubros": RUBROS_SRI,
        "padres": posibles_padres,
    }


def _leer(bd: Session, valores: dict, categoria: Categoria | None) -> tuple[dict, dict]:
    errores = f.Errores()
    datos: dict = {}
    datos["nombre"] = f.texto(valores, "nombre", 60, errores, obligatorio=True)
    tipo = str(valores.get("tipo") or "")
    if tipo not in ("gasto", "ingreso"):
        errores["tipo"] = "Elige gasto o ingreso."
    datos["tipo"] = tipo
    datos["icono"] = f.texto(valores, "icono", 16, errores)
    rubro = str(valores.get("rubro_sri") or "")
    if rubro and rubro not in RUBROS_SRI:
        errores["rubro_sri"] = "Rubro desconocido."
    datos["rubro_sri"] = rubro if (rubro and tipo == "gasto") else None
    padre_txt = str(valores.get("padre_id") or "")
    datos["padre_id"] = None
    if padre_txt.isdigit():
        padre = bd.get(Categoria, int(padre_txt))
        if padre is None or padre.tipo != tipo or padre.padre_id is not None or (categoria and padre.id == categoria.id):
            errores["padre_id"] = "La categoría principal debe ser del mismo tipo y no ser una subcategoría."
        else:
            datos["padre_id"] = padre.id
    datos["activa"] = f.marcado(valores, "activa")
    return datos, errores


@router.get("/nueva")
def nueva(request: Request, tipo: str = "gasto", bd: Session = Depends(obtener_bd)):
    valores = {"tipo": tipo if tipo in ("gasto", "ingreso") else "gasto", "activa": "1"}
    return render(request, "categorias/form.html", _contexto(bd, valores))


@router.post("/nueva", dependencies=[Depends(verificar_csrf)])
async def crear(request: Request, bd: Session = Depends(obtener_bd)):
    valores = dict(await request.form())
    datos, errores = _leer(bd, valores, None)
    if errores:
        return render(request, "categorias/form.html", _contexto(bd, valores, errores), status_code=422)
    orden = (bd.scalar(select(func.max(Categoria.orden))) or 0) + 1
    bd.add(Categoria(**datos, orden=orden))
    bd.commit()
    avisar(request, f"Categoría «{datos['nombre']}» creada.")
    return RedirectResponse("/categorias", status_code=303)


def _obtener(bd: Session, categoria_id: int) -> Categoria:
    cat = bd.get(Categoria, categoria_id)
    if cat is None:
        raise HTTPException(status_code=404, detail="Esa categoría no existe.")
    return cat


@router.get("/{categoria_id}/editar")
def editar(request: Request, categoria_id: int, bd: Session = Depends(obtener_bd)):
    c = _obtener(bd, categoria_id)
    valores = {
        "nombre": c.nombre,
        "tipo": c.tipo,
        "icono": c.icono or "",
        "rubro_sri": c.rubro_sri or "",
        "padre_id": str(c.padre_id or ""),
        "activa": "1" if c.activa else "",
    }
    usos = _usos(bd).get(c.id, 0)
    return render(request, "categorias/form.html", {**_contexto(bd, valores, categoria=c), "usos": usos})


@router.post("/{categoria_id}/editar", dependencies=[Depends(verificar_csrf)])
async def actualizar(request: Request, categoria_id: int, bd: Session = Depends(obtener_bd)):
    c = _obtener(bd, categoria_id)
    valores = dict(await request.form())
    datos, errores = _leer(bd, valores, c)
    usos = _usos(bd).get(c.id, 0)
    if usos and datos.get("tipo") and datos["tipo"] != c.tipo:
        errores["tipo"] = "Esta categoría ya tiene movimientos: no puede cambiar de tipo."
    tiene_hijas = bd.scalar(select(func.count()).select_from(Categoria).where(Categoria.padre_id == c.id))
    if tiene_hijas and datos.get("padre_id"):
        errores["padre_id"] = "Esta categoría tiene subcategorías: no puede ser subcategoría de otra."
    if errores:
        return render(
            request, "categorias/form.html", {**_contexto(bd, valores, errores, c), "usos": usos}, status_code=422
        )
    for campo, valor in datos.items():
        setattr(c, campo, valor)
    bd.commit()
    avisar(request, "Categoría actualizada.")
    return RedirectResponse("/categorias", status_code=303)


@router.post("/{categoria_id}/eliminar", dependencies=[Depends(verificar_csrf)])
def eliminar(request: Request, categoria_id: int, bd: Session = Depends(obtener_bd)):
    c = _obtener(bd, categoria_id)
    usos = _usos(bd).get(c.id, 0)
    hijas = bd.scalar(select(func.count()).select_from(Categoria).where(Categoria.padre_id == c.id))
    if usos or hijas:
        avisar(request, f"«{c.nombre}» está en uso. Desactívala en lugar de eliminarla.", "error")
        return RedirectResponse(f"/categorias/{c.id}/editar", status_code=303)
    bd.delete(c)
    bd.commit()
    avisar(request, "Categoría eliminada.")
    return RedirectResponse("/categorias", status_code=303)
