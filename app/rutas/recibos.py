"""Fase 2: fotos de recibos y XML del SRI (con confirmación), e importación de estados de cuenta."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import ia
from ..db import obtener_bd
from ..finanzas.montos import formato_usd
from ..finanzas.servicio import asegurar_categorias_base, cuenta_por_defecto, listar_categorias, listar_cuentas
from ..models import RUBROS_SRI, TIPOS_PASIVO, Adjunto, Categoria, Cuenta, Extraccion, Movimiento
from ..recibos import extraccion as ext
from ..recibos import importar as imp
from ..recibos import sri
from ..recibos.categorizar import aprender, sugerir_categoria
from ..seguridad import requiere_login, verificar_csrf
from ..tiempo import hoy
from ..web import avisar, render
from .movimientos import _alerta_presupuesto, _leer_y_validar

recibos = APIRouter(prefix="/recibos", dependencies=[Depends(requiere_login)])
importar = APIRouter(prefix="/importar", dependencies=[Depends(requiere_login)])

TAMANO_MAXIMO = 6 * 1024 * 1024


def _tipo_archivo(contenido: bytes) -> str | None:
    cabeza = contenido[:16]
    if cabeza.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if cabeza.startswith(b"\x89PNG"):
        return "image/png"
    if cabeza[:4] == b"RIFF" and cabeza[8:12] == b"WEBP":
        return "image/webp"
    if cabeza.startswith(b"GIF8"):
        return "image/gif"
    if contenido.lstrip(b"\xef\xbb\xbf \r\n\t").startswith(b"<"):
        return "application/xml"
    return None


def _procesar(bd: Session, extraccion: Extraccion, contenido: bytes, tipo: str) -> None:
    """Llena `extraccion.datos` desde el XML o la foto. Nunca lanza: deja el error anotado."""
    categorias = [c for c in listar_categorias(bd, "gasto", solo_activas=True)]
    nombres = [c.nombre for c in categorias] or ["Otros gastos"]
    try:
        if tipo == "application/xml":
            crudos = sri.parsear_xml(contenido)
            crudos["fuente_valores"] = "sri" if crudos.get("autorizado") else "xml"
        else:
            if not ia.configurada():
                raise ia.IANoConfigurada(
                    "La lectura automática de fotos necesita la clave ANTHROPIC_API_KEY. Puedes llenar los datos a mano."
                )
            crudos, extraccion.modelo = ext.extraer_de_imagen(contenido, tipo, nombres)
        datos = ext.normalizar(crudos, hoy())
        if datos.get("clave_acceso") and datos.get("fuente_valores") != "sri":
            completo = ext.completar_con_sri(datos)
            datos = ext.normalizar({**completo, "avisos": []}, hoy()) if completo.get("fuente_valores") == "sri" else completo
        extraccion.estado = "lista"
    except (sri.XMLInvalido, ia.IANoConfigurada, ia.ErrorIA) as exc:
        datos = ext.normalizar({}, hoy())
        datos["avisos"] = [str(exc)]
        extraccion.estado = "error"
        extraccion.error = str(exc)
    categoria_id = sugerir_categoria(bd, datos.get("comercio"), datos.get("categoria_sugerida"))
    categoria = bd.get(Categoria, categoria_id) if categoria_id else None
    datos["categoria_id"] = categoria_id
    datos["deducible"] = bool(categoria and categoria.rubro_sri and not datos.get("consumidor_final")
                              and datos.get("tipo_documento") in ("factura", None))
    extraccion.datos = datos


@recibos.get("")
def pagina(request: Request, bd: Session = Depends(obtener_bd)):
    asegurar_categorias_base(bd)
    pendientes = list(bd.scalars(
        select(Extraccion).where(Extraccion.estado.in_(("lista", "error")), Extraccion.fuente.in_(("foto", "xml")))
        .order_by(Extraccion.id.desc())
    ))
    return render(request, "recibos/pagina.html", {
        "seccion": "agregar",
        "pendientes": pendientes,
        "categorias": listar_categorias(bd, "gasto", solo_activas=True),
        "cuentas": listar_cuentas(bd, solo_activas=True),
        "cuenta_defecto": cuenta_por_defecto(bd, "gasto"),
        "rubros": RUBROS_SRI,
        "ia_activa": ia.configurada(),
        "modelo": ia.modelo(),
    })


@recibos.post("/subir", dependencies=[Depends(verificar_csrf)])
async def subir(request: Request, bd: Session = Depends(obtener_bd)):
    formulario = await request.form()
    archivos = [a for a in formulario.getlist("archivos") if hasattr(a, "read")]
    lote = str(formulario.get("lote") or uuid.uuid4().hex[:12])[:40]
    resultados = []
    for archivo in archivos[:20]:
        contenido = await archivo.read()
        nombre = (archivo.filename or "archivo")[:200]
        tipo = _tipo_archivo(contenido)
        if not contenido or len(contenido) > TAMANO_MAXIMO or tipo is None:
            resultados.append({"archivo": nombre, "ok": False, "error": "Archivo vacío, muy grande o de un tipo no admitido."})
            continue
        adjunto = Adjunto(nombre_archivo=nombre, tipo_mime=tipo, tamano=len(contenido), contenido=contenido)
        bd.add(adjunto)
        bd.flush()
        extraccion = Extraccion(fuente="xml" if tipo == "application/xml" else "foto", estado="pendiente",
                                adjunto_id=adjunto.id, lote=lote)
        bd.add(extraccion)
        _procesar(bd, extraccion, contenido, tipo)
        bd.commit()
        resultados.append({"archivo": nombre, "ok": extraccion.estado == "lista", "id": extraccion.id,
                           "error": extraccion.error})
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"resultados": resultados})
    leidos = sum(1 for r in resultados if r["ok"])
    avisar(request, f"{leidos} de {len(resultados)} comprobantes leídos. Revísalos y confírmalos.")
    return RedirectResponse("/recibos", status_code=303)


def _extraccion(bd: Session, eid: int) -> Extraccion:
    e = bd.get(Extraccion, eid)
    if e is None:
        raise HTTPException(404, "Ese comprobante no existe.")
    return e


@recibos.get("/{eid}/archivo")
def archivo(eid: int, bd: Session = Depends(obtener_bd)):
    e = _extraccion(bd, eid)
    if e.adjunto is None:
        raise HTTPException(404, "El archivo ya no está guardado.")
    tipo = "text/plain; charset=utf-8" if e.adjunto.tipo_mime == "application/xml" else e.adjunto.tipo_mime
    return Response(e.adjunto.contenido, media_type=tipo, headers={"Cache-Control": "private, max-age=3600"})


def _confirmar(request: Request, bd: Session, e: Extraccion, valores: dict) -> tuple[Movimiento | None, dict]:
    valores = {**valores, "tipo": "gasto"}
    datos, errores = _leer_y_validar(bd, valores, None)
    if errores:
        return None, errores
    mov = Movimiento(**datos, origen=e.fuente, adjunto_id=e.adjunto_id)
    bd.add(mov)
    bd.flush()
    e.estado, e.movimiento_id = "confirmada", mov.id
    aprender(bd, datos.get("comercio"), datos["categoria_id"])
    bd.commit()
    _alerta_presupuesto(request, bd, mov)
    return mov, {}


@recibos.post("/{eid}/confirmar", dependencies=[Depends(verificar_csrf)])
async def confirmar(request: Request, eid: int, bd: Session = Depends(obtener_bd)):
    e = _extraccion(bd, eid)
    if e.estado == "confirmada":
        return RedirectResponse("/recibos", status_code=303)
    mov, errores = _confirmar(request, bd, e, dict(await request.form()))
    if errores:
        avisar(request, "No se guardó: " + "; ".join(f"{k}: {v}" for k, v in errores.items()), "error")
    else:
        avisar(request, f"Gasto de {formato_usd(mov.monto)} en {mov.comercio or 'el comercio'} guardado.")
    return RedirectResponse("/recibos", status_code=303)


@recibos.post("/confirmar-todos", dependencies=[Depends(verificar_csrf)])
def confirmar_todos(request: Request, bd: Session = Depends(obtener_bd)):
    cuenta = cuenta_por_defecto(bd, "gasto")
    guardados = fallidos = 0
    for e in bd.scalars(select(Extraccion).where(Extraccion.estado == "lista", Extraccion.fuente.in_(("foto", "xml")))).all():
        d = e.datos or {}
        valores = {"monto": d.get("total") or "", "fecha": d.get("fecha") or "", "cuenta_id": str(cuenta or ""),
                   "categoria_gasto": str(d.get("categoria_id") or ""), "comercio": d.get("comercio") or "",
                   "subtotal": d.get("subtotal") or "", "iva": d.get("iva") or "", "ruc_emisor": d.get("ruc") or "",
                   "clave_acceso": d.get("clave_acceso") or "", "deducible": "1" if d.get("deducible") else ""}
        mov, errores = _confirmar(request, bd, e, valores)
        guardados, fallidos = (guardados + 1, fallidos) if mov else (guardados, fallidos + 1)
    avisar(request, f"{guardados} gastos guardados." + (f" {fallidos} necesitan revisión." if fallidos else ""))
    return RedirectResponse("/recibos", status_code=303)


@recibos.post("/{eid}/descartar", dependencies=[Depends(verificar_csrf)])
def descartar(request: Request, eid: int, bd: Session = Depends(obtener_bd)):
    e = _extraccion(bd, eid)
    e.estado = "descartada"
    if e.adjunto_id:
        adjunto = bd.get(Adjunto, e.adjunto_id)
        e.adjunto_id = None
        bd.flush()
        if adjunto and not bd.scalar(select(Movimiento.id).where(Movimiento.adjunto_id == adjunto.id)):
            bd.delete(adjunto)
    bd.commit()
    avisar(request, "Comprobante descartado.")
    return RedirectResponse("/recibos", status_code=303)


# =============================================================================
# Importación de estados de cuenta
# =============================================================================


@importar.get("")
def pagina_importar(request: Request, bd: Session = Depends(obtener_bd)):
    asegurar_categorias_base(bd)
    return render(request, "importar/subir.html", {"seccion": "mas", "cuentas": listar_cuentas(bd, solo_activas=True),
                                                    "error": None})


@importar.post("/analizar", dependencies=[Depends(verificar_csrf)])
async def analizar(request: Request, bd: Session = Depends(obtener_bd)):
    formulario = await request.form()
    archivo = formulario.get("archivo")
    cuenta_txt = str(formulario.get("cuenta_id") or "")
    cuenta = bd.get(Cuenta, int(cuenta_txt)) if cuenta_txt.isdigit() else None
    error = None
    if cuenta is None:
        error = "Elige la cuenta a la que pertenece el estado de cuenta."
    elif archivo is None or not hasattr(archivo, "read"):
        error = "Elige el archivo (.csv o .xlsx)."
    else:
        contenido = await archivo.read()
        if len(contenido) > TAMANO_MAXIMO:
            error = "El archivo es demasiado grande."
        else:
            try:
                filas = imp.leer_tabla(contenido, archivo.filename or "")
            except imp.ArchivoInvalido as exc:
                error = str(exc)
    if error:
        return render(request, "importar/subir.html", {"seccion": "mas", "cuentas": listar_cuentas(bd, solo_activas=True),
                                                        "error": error}, status_code=422)
    encabezado, columnas = imp.detectar_columnas(filas)
    adjunto = Adjunto(nombre_archivo=(archivo.filename or "estado")[:200], tipo_mime="text/csv",
                      tamano=len(contenido), contenido=contenido)
    bd.add(adjunto)
    bd.commit()
    ancho = max(len(f) for f in filas[: encabezado + 10])
    return render(request, "importar/mapear.html", {
        "seccion": "mas", "adjunto": adjunto, "cuenta": cuenta, "encabezado": encabezado, "columnas": columnas,
        "titulos": [(filas[encabezado][j] if j < len(filas[encabezado]) else f"Columna {j + 1}") or f"Columna {j + 1}"
                    for j in range(ancho)],
        "vista": filas[encabezado + 1 : encabezado + 9],
        "positivos_son_gastos": cuenta.tipo in TIPOS_PASIVO,
    })


@importar.post("/preparar", dependencies=[Depends(verificar_csrf)])
async def preparar(request: Request, bd: Session = Depends(obtener_bd)):
    formulario = dict(await request.form())
    adjunto = bd.get(Adjunto, int(formulario.get("adjunto_id", "0") or 0))
    cuenta = bd.get(Cuenta, int(formulario.get("cuenta_id", "0") or 0))
    if adjunto is None or cuenta is None:
        raise HTTPException(404, "El archivo ya no está disponible. Súbelo de nuevo.")
    filas = imp.leer_tabla(adjunto.contenido, adjunto.nombre_archivo)
    columnas = {c: (int(formulario[c]) if str(formulario.get(c, "")).isdigit() else None) for c in imp.CLAVES}
    encabezado = int(formulario.get("encabezado", "0") or 0)
    normalizadas, descartadas = imp.normalizar_filas(filas, encabezado, columnas,
                                                     str(formulario.get("positivos_son_gastos")) == "1")
    if not normalizadas:
        avisar(request, "No encontré filas con fecha y monto con esas columnas. Revisa la asignación.", "error")
        return RedirectResponse("/importar", status_code=303)
    huellas = imp.hashes(normalizadas, cuenta.id)
    existentes = set(bd.scalars(select(Movimiento.hash_importacion).where(Movimiento.hash_importacion.in_(huellas))))
    lote = uuid.uuid4().hex[:12]
    for fila, huella in zip(normalizadas, huellas):
        tipo = "gasto" if Decimal(fila["monto"]) < 0 else "ingreso"
        bd.add(Extraccion(fuente="importacion", estado="lista", lote=lote, adjunto_id=adjunto.id, datos={
            **fila, "hash": huella, "duplicado": huella in existentes, "cuenta_id": cuenta.id, "tipo": tipo,
            "categoria_id": sugerir_categoria(bd, fila["descripcion"], None, tipo),
        }))
    bd.commit()
    if descartadas:
        avisar(request, f"Se ignoraron {descartadas} filas sin fecha o monto (totales, saldos o encabezados).")
    return RedirectResponse(f"/importar/lote/{lote}", status_code=303)


@importar.get("/lote/{lote}")
def revisar(request: Request, lote: str, bd: Session = Depends(obtener_bd)):
    filas = list(bd.scalars(select(Extraccion).where(Extraccion.lote == lote, Extraccion.estado == "lista")
                            .order_by(Extraccion.id)))
    if not filas:
        avisar(request, "Ese lote ya fue importado o no existe.")
        return RedirectResponse("/movimientos", status_code=303)
    return render(request, "importar/revisar.html", {
        "seccion": "mas", "lote": lote, "filas": filas,
        "cuenta": bd.get(Cuenta, filas[0].datos["cuenta_id"]),
        "categorias_gasto": listar_categorias(bd, "gasto", solo_activas=True),
        "categorias_ingreso": listar_categorias(bd, "ingreso", solo_activas=True),
        "duplicados": sum(1 for f in filas if f.datos.get("duplicado")),
    })


@importar.post("/lote/{lote}", dependencies=[Depends(verificar_csrf)])
async def importar_lote(request: Request, lote: str, bd: Session = Depends(obtener_bd)):
    formulario = await request.form()
    elegidas = {int(x) for x in formulario.getlist("incluir") if str(x).isdigit()}
    creados = 0
    adjunto_id = None
    for e in bd.scalars(select(Extraccion).where(Extraccion.lote == lote, Extraccion.estado == "lista")).all():
        adjunto_id = e.adjunto_id
        d = e.datos
        if e.id not in elegidas or bd.scalar(select(Movimiento.id).where(Movimiento.hash_importacion == d["hash"])):
            e.estado = "descartada"
            continue
        cat_txt = str(formulario.get(f"categoria_{e.id}") or d.get("categoria_id") or "")
        categoria = bd.get(Categoria, int(cat_txt)) if cat_txt.isdigit() else None
        if categoria is None or categoria.tipo != d["tipo"]:
            categoria = bd.get(Categoria, sugerir_categoria(bd, None, None, d["tipo"]) or 0)
        mov = Movimiento(fecha=date.fromisoformat(d["fecha"]), tipo=d["tipo"],
                         monto=abs(Decimal(d["monto"])), cuenta_id=d["cuenta_id"],
                         categoria_id=categoria.id if categoria else None, comercio=d["descripcion"][:120],
                         origen="importacion", hash_importacion=d["hash"])
        bd.add(mov)
        bd.flush()
        e.estado, e.movimiento_id, e.adjunto_id = "confirmada", mov.id, None
        if categoria:
            aprender(bd, d["descripcion"], categoria.id)
        creados += 1
    for e in bd.scalars(select(Extraccion).where(Extraccion.lote == lote)).all():
        e.adjunto_id = None
    bd.flush()
    if adjunto_id:  # el archivo original ya no hace falta
        adjunto = bd.get(Adjunto, adjunto_id)
        if adjunto is not None:
            bd.delete(adjunto)
    bd.commit()
    avisar(request, f"{creados} movimientos importados.")
    return RedirectResponse("/movimientos", status_code=303)
