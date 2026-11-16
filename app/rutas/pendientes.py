"""Fase 3: vista Hoy, áreas, tareas, metas personales, fitness, estudio, postulaciones y becas."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from ..db import obtener_bd
from ..finanzas.planificacion import estado_presupuestos, proximos_pagos
from ..finanzas.servicio import listar_categorias, movs_para_calculo, padre_de, presupuestos_datos
from ..models import (
    ESTADOS_BECA,
    ESTADOS_POSTULACION,
    ESTADOS_TAREA,
    PLANTILLAS_AREA,
    PRIORIDADES,
    Area,
    Beca,
    BecaRequisito,
    Config,
    Entrenamiento,
    Informe,
    MetaPersonal,
    Postulacion,
    PostulacionEvento,
    Recurrente,
    Rutina,
    Serie,
    SesionEstudio,
    Tarea,
)
from ..pendientes import logica
from ..seguridad import requiere_login, verificar_csrf
from ..tiempo import ahora_utc, hoy
from ..web import avisar, render
from . import formularios as f

hoy_router = APIRouter(dependencies=[Depends(requiere_login)])
router = APIRouter(prefix="/pendientes", dependencies=[Depends(requiere_login)])

AREAS_BASE = [
    ("Trabajo", "💼", "general", None),
    ("Fitness", "🏋️", "fitness", {"entrenamientos_semana": 4}),
    ("Inglés", "🇬🇧", "idioma", {"horas_semana": 5, "nivel_actual": "B2", "nivel_objetivo": "C1"}),
    ("SQL y datos", "🗄️", "habilidad", {"horas_semana": 3, "nivel_actual": "Intermedio", "nivel_objetivo": "Avanzado"}),
    ("Postulaciones", "📨", "postulaciones", None),
    ("Becas de maestría", "🎓", "becas", None),
    ("Metas personales", "🎯", "metas", None),
]
REQUISITOS_BECA = ["IELTS / TOEFL", "Carta de recomendación 1", "Carta de recomendación 2", "Ensayo / carta de motivación",
                   "CV actualizado", "Título y notas (apostillados)", "Pasaporte vigente"]
DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]


def asegurar_areas_base(bd: Session) -> None:
    if bd.get(Config, "areas_base_creadas"):
        return
    if not bd.scalar(select(func.count()).select_from(Area)):
        for orden, (nombre, icono, plantilla, config) in enumerate(AREAS_BASE):
            bd.add(Area(nombre=nombre, icono=icono, plantilla=plantilla, config=config, orden=orden))
    bd.add(Config(clave="areas_base_creadas", valor="1"))
    bd.commit()


def _areas(bd: Session) -> list[Area]:
    return list(bd.scalars(select(Area).where(Area.activa.is_(True)).order_by(Area.orden, Area.id)))


def _tareas_abiertas(bd: Session, area_id: int | None = None) -> list[Tarea]:
    consulta = select(Tarea).options(joinedload(Tarea.area)).where(Tarea.estado.in_(("pendiente", "en_curso")),
                                                                   Tarea.padre_id.is_(None))
    if area_id:
        consulta = consulta.where(Tarea.area_id == area_id)
    tareas = list(bd.scalars(consulta).unique())
    orden = {t.id: i for i, t in enumerate(logica.ordenar_tareas(
        [logica.TareaDato(t.id, t.fecha_limite, t.prioridad, t.estado) for t in tareas], hoy()))}
    return sorted(tareas, key=lambda t: orden.get(t.id, 0))


def _estudio(bd: Session, area: Area, hoy_: date) -> dict:
    sesiones = [(s.fecha, s.minutos) for s in bd.scalars(select(SesionEstudio).where(SesionEstudio.area_id == area.id))]
    objetivo = int((area.config or {}).get("horas_semana") or 0) * 60
    return {
        "minutos_semana": logica.minutos_semana(sesiones, hoy_),
        "objetivo_minutos": objetivo,
        "racha": logica.racha({fecha for fecha, _ in sesiones}, hoy_),
        "horas_totales": round(sum(m for _, m in sesiones) / 60, 1),
        "hoy_estudiado": any(fecha == hoy_ for fecha, _ in sesiones),
    }


# =============================================================================
# Vista Hoy
# =============================================================================


@hoy_router.get("/")
def vista_hoy(request: Request, bd: Session = Depends(obtener_bd)):
    asegurar_areas_base(bd)
    hoy_ = hoy()
    abiertas = _tareas_abiertas(bd)
    para_hoy = [t for t in abiertas if logica.urgencia(t.fecha_limite, hoy_) <= 1]
    semana = [t for t in abiertas if logica.urgencia(t.fecha_limite, hoy_) in (2, 3)]

    activos = list(bd.scalars(select(Recurrente).options(joinedload(Recurrente.cuenta), joinedload(Recurrente.cuenta_destino))
                              .where(Recurrente.activa.is_(True))).unique())
    por_id = {r.id: r for r in activos}
    pagos = [(p, por_id[p.recurrente_id]) for p in proximos_pagos(((r.id, r.proxima_fecha, r.monto) for r in activos), hoy_, 7)]

    categorias = {c.id: c for c in listar_categorias(bd)}
    alertas = [(e, categorias.get(e.categoria_id)) for e in estado_presupuestos(
        presupuestos_datos(bd), movs_para_calculo(bd), hoy_.year, hoy_.month, padre_de(bd), hoy_) if e.nivel != "ok"]

    areas = _areas(bd)
    estudio = [(a, _estudio(bd, a, hoy_)) for a in areas if a.plantilla in ("idioma", "habilidad")]
    rutina = bd.scalar(select(Rutina).where(Rutina.dia_semana == hoy_.weekday()))
    entreno_hoy = bd.scalar(select(func.count()).select_from(Entrenamiento).where(Entrenamiento.fecha == hoy_))
    proximos_pasos = list(bd.scalars(select(Postulacion).where(
        Postulacion.estado.in_(("postulado", "entrevista", "oferta")), Postulacion.fecha_proximo_paso.is_not(None),
        Postulacion.fecha_proximo_paso <= hoy_ + timedelta(days=7)).order_by(Postulacion.fecha_proximo_paso)))
    becas = list(bd.scalars(select(Beca).options(joinedload(Beca.requisitos)).where(
        Beca.estado.in_(("investigando", "preparando")), Beca.fecha_limite.is_not(None),
        Beca.fecha_limite <= hoy_ + timedelta(days=45)).order_by(Beca.fecha_limite)).unique())
    suscripciones_por_revisar = bd.scalar(select(func.count()).select_from(Recurrente).where(
        Recurrente.activa.is_(True), Recurrente.tipo == "suscripcion",
        (Recurrente.revision_uso.is_(None)) | (Recurrente.revision_uso < hoy_.replace(day=1))))
    consejo = bd.scalar(select(Informe).where(Informe.tipo.in_(("semanal", "mensual"))).order_by(Informe.id.desc()))
    return render(request, "pendientes/hoy.html", {
        "seccion": "hoy", "hoy": hoy_, "para_hoy": para_hoy, "semana": semana, "pagos": pagos, "alertas": alertas,
        "estudio": estudio, "rutina": rutina, "entreno_hoy": entreno_hoy, "proximos_pasos": proximos_pasos,
        "becas": becas, "areas": areas, "suscripciones_por_revisar": suscripciones_por_revisar,
        "consejo": consejo, "urgencias": logica.URGENCIAS, "urgencia": logica.urgencia,
        "estados_postulacion": ESTADOS_POSTULACION,
    })


# =============================================================================
# Tablero y áreas
# =============================================================================


@router.get("")
def tablero(request: Request, bd: Session = Depends(obtener_bd)):
    asegurar_areas_base(bd)
    hoy_ = hoy()
    areas = _areas(bd)
    resumen = {}
    for a in areas:
        abiertas = _tareas_abiertas(bd, a.id)
        resumen[a.id] = {"abiertas": len(abiertas),
                         "vencidas": sum(1 for t in abiertas if logica.urgencia(t.fecha_limite, hoy_) == 0),
                         "proxima": abiertas[0] if abiertas else None}
    return render(request, "pendientes/tablero.html", {"seccion": "pendientes", "areas": areas, "resumen": resumen,
                                                        "plantillas": PLANTILLAS_AREA})


def _area(bd: Session, aid: int) -> Area:
    a = bd.get(Area, aid)
    if a is None:
        raise HTTPException(404, "Esa área no existe.")
    return a


@router.get("/area/{aid}")
def ver_area(request: Request, aid: int, bd: Session = Depends(obtener_bd)):
    a = _area(bd, aid)
    hoy_ = hoy()
    contexto = {
        "seccion": "pendientes", "area": a, "abiertas": _tareas_abiertas(bd, a.id), "hoy": hoy_,
        "hechas": list(bd.scalars(select(Tarea).where(Tarea.area_id == a.id, Tarea.estado == "hecha")
                                  .order_by(Tarea.completada_en.desc()).limit(10))),
        "prioridades": PRIORIDADES, "urgencia": logica.urgencia, "urgencias": logica.URGENCIAS,
    }
    if a.plantilla == "fitness":
        contexto["rutinas"] = {r.dia_semana: r for r in bd.scalars(select(Rutina))}
        contexto["dias"] = DIAS
        entrenos = list(bd.scalars(select(Entrenamiento).options(joinedload(Entrenamiento.series))
                                   .order_by(Entrenamiento.fecha.desc(), Entrenamiento.id.desc()).limit(8)).unique())
        contexto["entrenamientos"] = entrenos
        filas = bd.execute(select(Entrenamiento.fecha, Serie.ejercicio, Serie.repeticiones, Serie.peso)
                           .join(Serie, Serie.entrenamiento_id == Entrenamiento.id)).all()
        contexto["marcas"] = sorted(logica.mejores_marcas(filas).items())
        lunes = logica.inicio_semana(hoy_)
        contexto["entrenos_semana"] = bd.scalar(select(func.count()).select_from(Entrenamiento)
                                                .where(Entrenamiento.fecha >= lunes, Entrenamiento.fecha <= hoy_))
    elif a.plantilla in ("idioma", "habilidad"):
        contexto["estudio"] = _estudio(bd, a, hoy_)
        contexto["sesiones"] = list(bd.scalars(select(SesionEstudio).where(SesionEstudio.area_id == a.id)
                                               .order_by(SesionEstudio.fecha.desc(), SesionEstudio.id.desc()).limit(10)))
    elif a.plantilla == "postulaciones":
        postulaciones = list(bd.scalars(select(Postulacion).order_by(Postulacion.fecha_postulacion.desc())))
        contexto.update(postulaciones=postulaciones, embudo=logica.embudo(p.estado for p in postulaciones),
                        estados=ESTADOS_POSTULACION)
    elif a.plantilla == "becas":
        contexto.update(becas=list(bd.scalars(select(Beca).options(joinedload(Beca.requisitos))
                                              .order_by(Beca.fecha_limite.is_(None), Beca.fecha_limite)).unique()),
                        estados=ESTADOS_BECA)
    elif a.plantilla == "metas":
        metas = list(bd.scalars(select(MetaPersonal).order_by(MetaPersonal.periodo.desc(), MetaPersonal.id)))
        hitos: dict[int, list[Tarea]] = {}
        for t in bd.scalars(select(Tarea).where(Tarea.meta_personal_id.is_not(None)).order_by(Tarea.fecha_limite)):
            hitos.setdefault(t.meta_personal_id, []).append(t)
        contexto.update(metas=[(m, hitos.get(m.id, []), logica.progreso_hitos([t.estado for t in hitos.get(m.id, [])]))
                               for m in metas],
                        periodo_trimestre=logica.periodo_actual(hoy_, "trimestral"),
                        periodo_anio=logica.periodo_actual(hoy_, "anual"))
    return render(request, "pendientes/area.html", contexto)


def _leer_area(valores: dict) -> tuple[dict, dict]:
    errores = f.Errores()
    plantilla = str(valores.get("plantilla") or "general")
    if plantilla not in PLANTILLAS_AREA:
        errores["plantilla"] = "Elige un tipo."
    config = {}
    horas = f.entero(valores, "horas_semana", errores, 0, 80)
    if horas is not None:
        config["horas_semana"] = horas
    for campo in ("nivel_actual", "nivel_objetivo"):
        texto = f.texto(valores, campo, 40, errores)
        if texto:
            config[campo] = texto
    return {"nombre": f.texto(valores, "nombre", 60, errores, obligatorio=True), "icono": f.texto(valores, "icono", 16, errores),
            "plantilla": plantilla, "config": config or None}, errores


@router.get("/areas/nueva")
def nueva_area(request: Request):
    return render(request, "pendientes/area_form.html", {"seccion": "pendientes", "valores": {"plantilla": "general"},
                                                          "errores": {}, "area": None, "plantillas": PLANTILLAS_AREA})


@router.post("/areas/nueva", dependencies=[Depends(verificar_csrf)])
async def crear_area(request: Request, bd: Session = Depends(obtener_bd)):
    valores = dict(await request.form())
    datos, errores = _leer_area(valores)
    if errores:
        return render(request, "pendientes/area_form.html", {"seccion": "pendientes", "valores": valores, "errores": errores,
                                                              "area": None, "plantillas": PLANTILLAS_AREA}, status_code=422)
    area = Area(**datos, orden=(bd.scalar(select(func.max(Area.orden))) or 0) + 1)
    bd.add(area)
    bd.commit()
    avisar(request, f"Área «{area.nombre}» creada.")
    return RedirectResponse(f"/pendientes/area/{area.id}", status_code=303)


@router.get("/area/{aid}/editar")
def editar_area(request: Request, aid: int, bd: Session = Depends(obtener_bd)):
    a = _area(bd, aid)
    valores = {"nombre": a.nombre, "icono": a.icono or "", "plantilla": a.plantilla, **{k: str(v) for k, v in (a.config or {}).items()}}
    return render(request, "pendientes/area_form.html", {"seccion": "pendientes", "valores": valores, "errores": {},
                                                          "area": a, "plantillas": PLANTILLAS_AREA})


@router.post("/area/{aid}/editar", dependencies=[Depends(verificar_csrf)])
async def actualizar_area(request: Request, aid: int, bd: Session = Depends(obtener_bd)):
    a = _area(bd, aid)
    valores = dict(await request.form())
    datos, errores = _leer_area(valores)
    if errores:
        return render(request, "pendientes/area_form.html", {"seccion": "pendientes", "valores": valores, "errores": errores,
                                                              "area": a, "plantillas": PLANTILLAS_AREA}, status_code=422)
    for campo, valor in datos.items():
        setattr(a, campo, valor)
    if str(valores.get("archivar")) == "1":
        a.activa = False
    bd.commit()
    avisar(request, "Área actualizada." if a.activa else "Área archivada.")
    return RedirectResponse(f"/pendientes/area/{a.id}" if a.activa else "/pendientes", status_code=303)


# =============================================================================
# Tareas
# =============================================================================


def _leer_tarea(bd: Session, valores: dict) -> tuple[dict, dict]:
    errores = f.Errores()
    area_txt = str(valores.get("area_id") or "")
    area = bd.get(Area, int(area_txt)) if area_txt.isdigit() else None
    if area is None:
        errores["area_id"] = "Elige un área."
    prioridad = f.entero(valores, "prioridad", errores, 1, 3) or 2
    recurrencia = str(valores.get("recurrencia") or "") or None
    if recurrencia not in (None, "diaria", "semanal", "mensual"):
        errores["recurrencia"] = "Recurrencia inválida."
    meta_txt = str(valores.get("meta_personal_id") or "")
    return {
        "area_id": area.id if area else None,
        "titulo": f.texto(valores, "titulo", 200, errores, obligatorio=True),
        "descripcion": f.texto(valores, "descripcion", 2000, errores),
        "fecha_limite": f.fecha(valores, "fecha_limite", errores, obligatorio=False),
        "prioridad": prioridad,
        "recurrencia": recurrencia,
        "meta_personal_id": int(meta_txt) if meta_txt.isdigit() and bd.get(MetaPersonal, int(meta_txt)) else None,
    }, errores


def _volver(request: Request, por_defecto: str) -> RedirectResponse:
    destino = str(request.headers.get("referer") or "")
    base = str(request.base_url)
    return RedirectResponse(destino if destino.startswith(base) else por_defecto, status_code=303)


@router.post("/tareas/nueva", dependencies=[Depends(verificar_csrf)])
async def crear_tarea(request: Request, bd: Session = Depends(obtener_bd)):
    valores = dict(await request.form())
    datos, errores = _leer_tarea(bd, valores)
    if errores:
        avisar(request, "No se creó la tarea: " + "; ".join(errores.values()), "error")
    else:
        bd.add(Tarea(**datos))
        bd.commit()
        avisar(request, f"Tarea «{datos['titulo']}» agregada.")
    return _volver(request, "/")


def _tarea(bd: Session, tid: int) -> Tarea:
    t = bd.get(Tarea, tid)
    if t is None:
        raise HTTPException(404, "Esa tarea no existe.")
    return t


@router.post("/tareas/{tid}/hecha", dependencies=[Depends(verificar_csrf)])
def marcar_hecha(request: Request, tid: int, bd: Session = Depends(obtener_bd)):
    t = _tarea(bd, tid)
    if t.estado == "hecha":
        t.estado, t.completada_en = "pendiente", None
        avisar(request, f"«{t.titulo}» vuelve a pendientes.")
    else:
        t.estado, t.completada_en = "hecha", ahora_utc()
        siguiente = logica.siguiente_ocurrencia(t.fecha_limite or hoy(), t.recurrencia)
        if siguiente:
            bd.add(Tarea(area_id=t.area_id, titulo=t.titulo, descripcion=t.descripcion, fecha_limite=siguiente,
                         prioridad=t.prioridad, recurrencia=t.recurrencia, meta_personal_id=t.meta_personal_id))
        avisar(request, f"✓ «{t.titulo}» completada." + (f" La próxima es el {siguiente:%d/%m}." if siguiente else ""))
    bd.commit()
    return _volver(request, "/")


@router.get("/tareas/{tid}/editar")
def editar_tarea(request: Request, tid: int, bd: Session = Depends(obtener_bd)):
    t = _tarea(bd, tid)
    valores = {"area_id": str(t.area_id), "titulo": t.titulo, "descripcion": t.descripcion or "",
               "fecha_limite": t.fecha_limite.isoformat() if t.fecha_limite else "", "prioridad": str(t.prioridad),
               "recurrencia": t.recurrencia or "", "meta_personal_id": str(t.meta_personal_id or ""), "estado": t.estado}
    return render(request, "pendientes/tarea_form.html", {
        "seccion": "pendientes", "tarea": t, "valores": valores, "errores": {}, "areas": _areas(bd),
        "metas": list(bd.scalars(select(MetaPersonal).where(MetaPersonal.estado == "activa"))),
        "prioridades": PRIORIDADES, "estados": ESTADOS_TAREA})


@router.post("/tareas/{tid}/editar", dependencies=[Depends(verificar_csrf)])
async def actualizar_tarea(request: Request, tid: int, bd: Session = Depends(obtener_bd)):
    t = _tarea(bd, tid)
    valores = dict(await request.form())
    datos, errores = _leer_tarea(bd, valores)
    estado = str(valores.get("estado") or t.estado)
    if estado not in ESTADOS_TAREA:
        errores["estado"] = "Estado inválido."
    if errores:
        return render(request, "pendientes/tarea_form.html", {
            "seccion": "pendientes", "tarea": t, "valores": valores, "errores": errores, "areas": _areas(bd),
            "metas": list(bd.scalars(select(MetaPersonal).where(MetaPersonal.estado == "activa"))),
            "prioridades": PRIORIDADES, "estados": ESTADOS_TAREA}, status_code=422)
    for campo, valor in datos.items():
        setattr(t, campo, valor)
    if estado != t.estado:
        t.estado = estado
        t.completada_en = ahora_utc() if estado == "hecha" else None
    bd.commit()
    avisar(request, "Tarea actualizada.")
    return RedirectResponse(f"/pendientes/area/{t.area_id}", status_code=303)


@router.post("/tareas/{tid}/eliminar", dependencies=[Depends(verificar_csrf)])
def eliminar_tarea(request: Request, tid: int, bd: Session = Depends(obtener_bd)):
    t = _tarea(bd, tid)
    area_id = t.area_id
    for hija in bd.scalars(select(Tarea).where(Tarea.padre_id == t.id)).all():
        hija.padre_id = None
    bd.delete(t)
    bd.commit()
    avisar(request, "Tarea eliminada.")
    return RedirectResponse(f"/pendientes/area/{area_id}", status_code=303)


# =============================================================================
# Registros por plantilla
# =============================================================================


@router.post("/estudio/{aid}", dependencies=[Depends(verificar_csrf)])
async def registrar_estudio(request: Request, aid: int, bd: Session = Depends(obtener_bd)):
    a = _area(bd, aid)
    valores = dict(await request.form())
    errores = f.Errores()
    minutos = f.entero(valores, "minutos", errores, 1, 900)
    fecha = f.fecha(valores, "fecha", errores, obligatorio=False) or hoy()
    if errores or not minutos:
        avisar(request, "Escribe los minutos estudiados.", "error")
    else:
        bd.add(SesionEstudio(area_id=a.id, fecha=fecha, minutos=minutos, tema=f.texto(valores, "tema", 200, errores)))
        bd.commit()
        avisar(request, f"{minutos} minutos de {a.nombre} registrados. ¡Sigue la racha!")
    return _volver(request, f"/pendientes/area/{a.id}")


@router.post("/fitness/entrenamiento", dependencies=[Depends(verificar_csrf)])
async def registrar_entrenamiento(request: Request, bd: Session = Depends(obtener_bd)):
    formulario = await request.form()
    errores = f.Errores()
    valores = dict(formulario)
    fecha = f.fecha(valores, "fecha", errores, obligatorio=False) or hoy()
    entreno = Entrenamiento(fecha=fecha, rutina=f.texto(valores, "rutina", 80, errores),
                            duracion_min=f.entero(valores, "duracion_min", errores, 1, 600),
                            nota=f.texto(valores, "nota", 1000, errores))
    ejercicios, repeticiones, pesos = (formulario.getlist(c) for c in ("ejercicio", "repeticiones", "peso"))
    for ejercicio, reps, peso in zip(ejercicios, repeticiones, pesos):
        if str(ejercicio).strip() and str(reps).strip().isdigit():
            try:
                peso_dec = Decimal(str(peso).replace(",", ".")) if str(peso).strip() else None
            except ArithmeticError:
                peso_dec = None
            entreno.series.append(Serie(ejercicio=str(ejercicio).strip()[:80], repeticiones=int(reps), peso=peso_dec))
    bd.add(entreno)
    bd.commit()
    avisar(request, f"Entrenamiento registrado ({len(entreno.series)} series).")
    return _volver(request, "/pendientes")


@router.post("/fitness/rutina", dependencies=[Depends(verificar_csrf)])
async def guardar_rutina(request: Request, bd: Session = Depends(obtener_bd)):
    formulario = await request.form()
    for dia in range(7):
        nombre = str(formulario.get(f"nombre_{dia}") or "").strip()[:80]
        ejercicios = str(formulario.get(f"ejercicios_{dia}") or "").strip()[:2000]
        rutina = bd.scalar(select(Rutina).where(Rutina.dia_semana == dia))
        if not nombre and not ejercicios:
            if rutina:
                bd.delete(rutina)
            continue
        if rutina is None:
            rutina = Rutina(dia_semana=dia)
            bd.add(rutina)
        rutina.nombre, rutina.ejercicios = nombre or DIAS[dia], ejercicios
    bd.commit()
    avisar(request, "Rutina semanal guardada.")
    return _volver(request, "/pendientes")


@router.post("/postulaciones", dependencies=[Depends(verificar_csrf)])
@router.post("/postulaciones/{pid}", dependencies=[Depends(verificar_csrf)])
async def guardar_postulacion(request: Request, pid: int | None = None, bd: Session = Depends(obtener_bd)):
    valores = dict(await request.form())
    errores = f.Errores()
    estado = str(valores.get("estado") or "postulado")
    if estado not in ESTADOS_POSTULACION:
        errores["estado"] = "Estado inválido."
    datos = {
        "empresa": f.texto(valores, "empresa", 120, errores, obligatorio=True),
        "cargo": f.texto(valores, "cargo", 160, errores, obligatorio=True),
        "enlace": f.texto(valores, "enlace", 500, errores),
        "fecha_postulacion": f.fecha(valores, "fecha_postulacion", errores, obligatorio=False) or hoy(),
        "proximo_paso": f.texto(valores, "proximo_paso", 200, errores),
        "fecha_proximo_paso": f.fecha(valores, "fecha_proximo_paso", errores, obligatorio=False),
        "salario": f.texto(valores, "salario", 60, errores),
        "notas": f.texto(valores, "notas", 3000, errores),
    }
    if datos["enlace"] and not datos["enlace"].startswith(("http://", "https://")):
        errores["enlace"] = "El enlace debe empezar por https://"
    if errores:
        avisar(request, "No se guardó la postulación: " + "; ".join(errores.values()), "error")
        return _volver(request, "/pendientes")
    postulacion = bd.get(Postulacion, pid) if pid else None
    if pid and postulacion is None:
        raise HTTPException(404, "Esa postulación no existe.")
    if postulacion is None:
        postulacion = Postulacion(**datos, estado=estado)
        bd.add(postulacion)
        bd.flush()
        bd.add(PostulacionEvento(postulacion_id=postulacion.id, fecha=datos["fecha_postulacion"], estado=estado))
    else:
        if estado != postulacion.estado:
            bd.add(PostulacionEvento(postulacion_id=postulacion.id, fecha=hoy(), estado=estado,
                                     nota=(datos["proximo_paso"] or "")[:300] or None))
        for campo, valor in datos.items():
            setattr(postulacion, campo, valor)
        postulacion.estado = estado
    bd.commit()
    avisar(request, f"Postulación a {postulacion.empresa} guardada.")
    return _volver(request, "/pendientes")


@router.post("/becas", dependencies=[Depends(verificar_csrf)])
@router.post("/becas/{bid}", dependencies=[Depends(verificar_csrf)])
async def guardar_beca(request: Request, bid: int | None = None, bd: Session = Depends(obtener_bd)):
    valores = dict(await request.form())
    errores = f.Errores()
    estado = str(valores.get("estado") or "investigando")
    if estado not in ESTADOS_BECA:
        errores["estado"] = "Estado inválido."
    datos = {
        "programa": f.texto(valores, "programa", 160, errores, obligatorio=True),
        "universidad": f.texto(valores, "universidad", 160, errores),
        "pais": f.texto(valores, "pais", 60, errores),
        "beca": f.texto(valores, "beca", 160, errores),
        "enlace": f.texto(valores, "enlace", 500, errores),
        "fecha_limite": f.fecha(valores, "fecha_limite", errores, obligatorio=False),
        "notas": f.texto(valores, "notas", 3000, errores),
    }
    if errores:
        avisar(request, "No se guardó la beca: " + "; ".join(errores.values()), "error")
        return _volver(request, "/pendientes")
    beca = bd.get(Beca, bid) if bid else None
    if beca is None:
        beca = Beca(**datos, estado=estado)
        for orden, requisito in enumerate(REQUISITOS_BECA):
            beca.requisitos.append(BecaRequisito(requisito=requisito, orden=orden))
        bd.add(beca)
    else:
        for campo, valor in datos.items():
            setattr(beca, campo, valor)
        beca.estado = estado
    nuevo = f.texto(valores, "nuevo_requisito", 200, errores)
    if nuevo:
        beca.requisitos.append(BecaRequisito(requisito=nuevo, orden=len(beca.requisitos)))
    bd.commit()
    avisar(request, f"Beca «{beca.programa}» guardada.")
    return _volver(request, "/pendientes")


@router.post("/becas/requisito/{rid}", dependencies=[Depends(verificar_csrf)])
def alternar_requisito(request: Request, rid: int, bd: Session = Depends(obtener_bd)):
    requisito = bd.get(BecaRequisito, rid)
    if requisito is None:
        raise HTTPException(404, "Ese requisito no existe.")
    requisito.hecho = not requisito.hecho
    bd.commit()
    return _volver(request, "/pendientes")


@router.post("/metas", dependencies=[Depends(verificar_csrf)])
async def crear_meta_personal(request: Request, bd: Session = Depends(obtener_bd)):
    valores = dict(await request.form())
    errores = f.Errores()
    horizonte = "anual" if valores.get("horizonte") == "anual" else "trimestral"
    titulo = f.texto(valores, "titulo", 200, errores, obligatorio=True)
    area_txt = str(valores.get("area_id") or "")
    if errores:
        avisar(request, "Escribe la meta.", "error")
    else:
        bd.add(MetaPersonal(titulo=titulo, horizonte=horizonte, periodo=logica.periodo_actual(hoy(), horizonte),
                            descripcion=f.texto(valores, "descripcion", 2000, errores),
                            area_id=int(area_txt) if area_txt.isdigit() else None))
        bd.commit()
        avisar(request, "Meta creada. Agrégale hitos (tareas) para medir el avance.")
    return _volver(request, "/pendientes")


@router.post("/metas/{mid}/estado", dependencies=[Depends(verificar_csrf)])
async def estado_meta_personal(request: Request, mid: int, bd: Session = Depends(obtener_bd)):
    meta = bd.get(MetaPersonal, mid)
    if meta is None:
        raise HTTPException(404, "Esa meta no existe.")
    estado = str((await request.form()).get("estado") or "")
    if estado in ("activa", "cumplida", "abandonada"):
        meta.estado = estado
        bd.commit()
    return _volver(request, "/pendientes")
