"""Descargar un respaldo completo y restaurarlo."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..db import obtener_bd
from ..respaldo import RespaldoInvalido, a_zip, exportar, leer_archivo, restaurar, tablas_respaldables
from ..seguridad import requiere_login, verificar_csrf
from ..tiempo import ahora_quito
from ..web import avisar, render

router = APIRouter(prefix="/respaldo", dependencies=[Depends(requiere_login)])


@router.get("")
def pagina(request: Request, bd: Session = Depends(obtener_bd)):
    conteos = {t.name: bd.scalar(select(func.count()).select_from(t)) for t in tablas_respaldables()}
    return render(request, "respaldo.html", {"seccion": "mas", "conteos": conteos, "error": None})


@router.get("/descargar")
def descargar(bd: Session = Depends(obtener_bd)):
    contenido = a_zip(exportar(bd))
    nombre = f"mi-centro-de-control-{ahora_quito():%Y%m%d-%H%M}.zip"
    return Response(
        contenido,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"', "Cache-Control": "no-store"},
    )


@router.post("/restaurar", dependencies=[Depends(verificar_csrf)])
async def restaurar_respaldo(request: Request, bd: Session = Depends(obtener_bd)):
    formulario = await request.form()
    archivo = formulario.get("archivo")
    error = None
    if str(formulario.get("confirmacion") or "").strip().upper() != "REEMPLAZAR":
        error = "Escribe REEMPLAZAR para confirmar."
    elif archivo is None or not hasattr(archivo, "read"):
        error = "Elige el archivo de respaldo (.zip o .json)."
    else:
        try:
            datos = leer_archivo(await archivo.read())
            conteo = restaurar(bd, datos)
        except RespaldoInvalido as exc:
            error = str(exc)
        except SQLAlchemyError:
            error = "No se pudo restaurar: el respaldo tiene datos inconsistentes. No se cambió nada."
        else:
            total = sum(conteo.values())
            avisar(request, f"Respaldo restaurado: {total} registros.")
            return RedirectResponse("/", status_code=303)
    conteos = {t.name: bd.scalar(select(func.count()).select_from(t)) for t in tablas_respaldables()}
    return render(request, "respaldo.html", {"seccion": "mas", "conteos": conteos, "error": error}, status_code=422)
