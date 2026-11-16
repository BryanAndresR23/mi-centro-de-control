"""Genera los informes del asesor (semanal y mensual) y la revisión semanal de pendientes."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import ia
from ..finanzas.montos import formato_usd
from ..finanzas.planificacion import costo_mensual, estado_presupuestos
from ..finanzas.servicio import listar_categorias, padre_de, presupuestos_datos, progreso_metas
from ..models import (
    Area,
    Beca,
    Entrenamiento,
    Informe,
    Movimiento,
    PostulacionEvento,
    Recurrente,
    SesionEstudio,
    Tarea,
)
from ..pendientes import logica
from ..tiempo import nombre_mes, primer_dia, sumar_meses, ultimo_dia
from . import hechos as h

SISTEMA_ASESOR = """Eres un asesor de finanzas personales para una persona que vive en Quito, Ecuador (USD).
Recibes HECHOS ya calculados por el sistema a partir de sus datos reales. Reglas estrictas:
- Usa solo los números que aparecen en los hechos; no inventes cifras, porcentajes ni fechas.
- Da entre 2 y 4 consejos concretos y accionables, en español, tuteando, sin frases genéricas.
- Cada consejo debe citar los ids de los hechos en que se basa.
- No recomiendes productos financieros específicos ni inversiones; no eres un asesor licenciado.
- El resumen: 1 o 2 frases con lo más importante del período."""


def _esquema_consejos() -> dict:
    return {
        "type": "object",
        "properties": {
            "resumen": {"type": "string"},
            "consejos": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "titulo": {"type": "string"},
                        "detalle": {"type": "string"},
                        "hechos": {"type": "array", "items": {"type": "string"}},
                        "ahorro_mensual_usd": {"anyOf": [{"type": "number"}, {"type": "null"}]},
                    },
                    "required": ["titulo", "detalle", "hechos", "ahorro_mensual_usd"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["resumen", "consejos"],
        "additionalProperties": False,
    }


def _consejos_sin_ia(hechos: list[dict]) -> tuple[str, list[dict]]:
    """Respaldo sin IA: los hechos más importantes, con una acción sugerida por tipo."""
    acciones = {
        "categoria_sube": "Ponle un presupuesto a esta categoría o revisa qué cambió este período.",
        "hormiga": "Fija un tope semanal para compras pequeñas o llévalas en efectivo.",
        "suscripcion_sin_uso": "Cancélala o pásala a un plan más barato.",
        "tasa_ahorro": "Programa un Ahorro apenas recibas tu sueldo.",
        "meta": "Haz el recorte sugerido y registra el ahorro asignado a la meta.",
        "meta_sin_ritmo": "Agenda un aporte fijo mensual a esta meta.",
        "ingreso_extra": "Asigna una parte a tus metas antes de gastarlo.",
        "presupuesto": "Frena el gasto en esta categoría lo que queda del mes.",
        "pago_vencido": "Regístralo como pagado o págalo hoy para evitar intereses.",
    }
    titulos = {
        "categoria_sube": "Revisa {categoria}", "hormiga": "Ojo con los gastos hormiga", "suscripcion_sin_uso": "Cancela lo que no usas",
        "tasa_ahorro": "Tu tasa de ahorro", "meta": "Acelera tu meta", "meta_sin_ritmo": "Retoma tu meta",
        "ingreso_extra": "Aprovecha el ingreso extra", "presupuesto": "Cuida tu presupuesto", "pago_vencido": "Pago pendiente",
    }
    relevantes = [x for x in hechos if x.get("tipo") in acciones][:4]
    consejos = [{"titulo": titulos[x["tipo"]].format(categoria=x.get("categoria", "esta categoría")),
                 "detalle": f"{x['texto']} {acciones[x['tipo']]}", "hechos": [x["id"]], "ahorro_mensual_usd": None}
                for x in relevantes]
    resumen = hechos[0]["texto"] if hechos else "Aún no hay suficientes datos para analizar este período."
    return resumen, consejos


def _con_ia(hechos: list[dict], contexto: str) -> tuple[str, list[dict], str | None]:
    if not ia.configurada() or not hechos:
        return (*_consejos_sin_ia(hechos), None)
    try:
        datos, modelo = ia.pedir_json(SISTEMA_ASESOR, f"{contexto}\nHECHOS:\n{json.dumps(hechos, ensure_ascii=False)}",
                                      _esquema_consejos(), max_tokens=4000, esfuerzo="medium")
    except (ia.ErrorIA, ia.IANoConfigurada):
        return (*_consejos_sin_ia(hechos), None)
    ids = {x["id"] for x in hechos}
    consejos = [c for c in datos.get("consejos", []) if c.get("hechos") and set(c["hechos"]) <= ids]
    if not consejos:
        return (*_consejos_sin_ia(hechos), None)
    return datos.get("resumen") or "", consejos, modelo


def _datos_financieros(bd: Session, periodo: tuple[date, date], base: tuple[date, date], hoy: date) -> list[dict]:
    from ..finanzas.servicio import movs_para_calculo  # noqa: PLC0415

    filas = bd.execute(select(Movimiento.fecha, Movimiento.tipo, Movimiento.monto, Movimiento.cuenta_id,
                              Movimiento.cuenta_destino_id, Movimiento.categoria_id, Movimiento.comercio)).all()
    detalle = [h.MovDetalle(*f) for f in filas]
    movs = movs_para_calculo(bd)
    padres = padre_de(bd)
    nombres = {c.id: c.nombre for c in listar_categorias(bd)}
    lista: list[dict] = []
    lista += h.comparar_categorias(movs, periodo, base, padres, nombres)[:4]
    for hecho in (h.gastos_hormiga(detalle, periodo), h.resumen_ahorro(movs, periodo, base),
                  h.ingresos_extra(movs, periodo, nombres)):
        if hecho:
            lista.append(hecho)
    for e in estado_presupuestos(presupuestos_datos(bd), movs, hoy.year, hoy.month, padres, hoy):
        if e.nivel != "ok":
            lista.append({"tipo": "presupuesto", "impacto_usd": str(max(e.gastado - e.monto, 0)), "prioridad": 1,
                          "texto": f"Llevas {formato_usd(e.gastado)} de {formato_usd(e.monto)} del presupuesto de "
                                   f"{nombres.get(e.categoria_id, '')} este mes ({int(e.pct * 100)} %)."})
    metas = [(m.nombre, p.objetivo, p.total, p.ritmo_mensual, m.fecha_objetivo, p.meses_estimados)
             for m, p in progreso_metas(bd, hoy)]
    lista += h.metas_y_recortes(metas, movs, hoy, padres, nombres)
    recurrentes = list(bd.scalars(select(Recurrente).where(Recurrente.activa.is_(True))))
    lista += h.suscripciones([(r.nombre, costo_mensual(r.monto, r.frecuencia), r.meses_sin_uso)
                              for r in recurrentes if r.tipo == "suscripcion"])
    for r in recurrentes:
        if r.proxima_fecha < hoy:
            lista.append({"tipo": "pago_vencido", "impacto_usd": str(r.monto), "prioridad": 1,
                          "texto": f"El pago de {r.nombre} venció el {r.proxima_fecha:%d/%m} y no está registrado."})
    return h.numerar(lista)


def generar_financiero(bd: Session, tipo: str, hoy: date) -> Informe:
    if tipo == "semanal":
        periodo = (hoy - timedelta(days=7), hoy - timedelta(days=1))
        base = (hoy - timedelta(days=63), hoy - timedelta(days=8))
        etiqueta = f"{periodo[0]:%d/%m} – {periodo[1]:%d/%m/%Y}"
        contexto = f"Análisis SEMANAL del {etiqueta}, comparado con el promedio de las 8 semanas anteriores."
    else:
        a, m = sumar_meses(hoy.year, hoy.month, -1)
        periodo = (primer_dia(a, m), ultimo_dia(a, m))
        a0, m0 = sumar_meses(a, m, -3)
        base = (primer_dia(a0, m0), ultimo_dia(*sumar_meses(a, m, -1)))
        etiqueta = nombre_mes(a, m)
        contexto = f"Análisis MENSUAL de {etiqueta}, comparado con el promedio de los 3 meses anteriores."
    hechos = _datos_financieros(bd, periodo, base, hoy)
    resumen, consejos, modelo = _con_ia(hechos, contexto)
    informe = Informe(tipo=tipo, periodo=etiqueta, hechos={"hechos": hechos, "consejos": consejos},
                      texto=resumen, modelo=modelo)
    bd.add(informe)
    bd.commit()
    return informe


def generar_revision(bd: Session, hoy: date) -> Informe:
    """Revisión semanal: lo completado, lo pendiente y qué priorizar."""
    desde = hoy - timedelta(days=7)
    inicio = datetime(desde.year, desde.month, desde.day)
    areas = {a.id: a for a in bd.scalars(select(Area))}
    hechas = list(bd.scalars(select(Tarea).where(Tarea.estado == "hecha", Tarea.completada_en >= inicio)))
    abiertas = list(bd.scalars(select(Tarea).where(Tarea.estado.in_(("pendiente", "en_curso")))))
    vencidas = [t for t in abiertas if logica.urgencia(t.fecha_limite, hoy) == 0]
    proximas = [t for t in abiertas if logica.urgencia(t.fecha_limite, hoy) in (1, 2, 3)]
    lista: list[dict] = []
    por_area: dict[str, int] = {}
    for t in hechas:
        nombre = areas[t.area_id].nombre if t.area_id in areas else "General"
        por_area[nombre] = por_area.get(nombre, 0) + 1
    if hechas:
        lista.append({"tipo": "completadas", "impacto_usd": None, "prioridad": 3,
                      "texto": f"Completaste {len(hechas)} tareas: " + ", ".join(f"{n} ({c})" for n, c in por_area.items()) + "."})
    if vencidas:
        lista.append({"tipo": "vencidas", "impacto_usd": None, "prioridad": 1,
                      "texto": f"Tienes {len(vencidas)} tareas vencidas: " + "; ".join(t.titulo for t in vencidas[:5]) + "."})
    for a in areas.values():
        if a.plantilla in ("idioma", "habilidad") and a.activa:
            sesiones = [(s.fecha, s.minutos) for s in bd.scalars(select(SesionEstudio).where(SesionEstudio.area_id == a.id))]
            minutos = sum(m for f, m in sesiones if desde <= f <= hoy)
            objetivo = int((a.config or {}).get("horas_semana") or 0) * 60
            if objetivo:
                lista.append({"tipo": "estudio", "impacto_usd": None, "prioridad": 1 if minutos < objetivo * 0.6 else 3,
                              "texto": f"{a.nombre}: {minutos / 60:.1f} h de {objetivo / 60:.0f} h objetivo; racha de "
                                       f"{logica.racha({f for f, _ in sesiones}, hoy)} días."})
    entrenos = len(list(bd.scalars(select(Entrenamiento.id).where(Entrenamiento.fecha >= desde, Entrenamiento.fecha <= hoy))))
    lista.append({"tipo": "fitness", "impacto_usd": None, "prioridad": 2 if entrenos < 3 else 3,
                  "texto": f"Entrenaste {entrenos} veces en los últimos 7 días."})
    eventos = list(bd.scalars(select(PostulacionEvento).where(PostulacionEvento.fecha >= desde)))
    if eventos:
        lista.append({"tipo": "postulaciones", "impacto_usd": None, "prioridad": 2,
                      "texto": f"{len(eventos)} movimientos en tus postulaciones esta semana."})
    for b in bd.scalars(select(Beca).where(Beca.fecha_limite.is_not(None), Beca.estado.in_(("investigando", "preparando")))):
        dias = (b.fecha_limite - hoy).days
        if 0 <= dias <= 30:
            pendientes = sum(1 for r in b.requisitos if not r.hecho)
            lista.append({"tipo": "beca", "impacto_usd": None, "prioridad": 1,
                          "texto": f"La beca {b.programa} cierra en {dias} días y te faltan {pendientes} requisitos."})
    hechos = h.numerar(lista)
    prioridades = [t.titulo for t in sorted(vencidas + proximas, key=lambda t: (logica.urgencia(t.fecha_limite, hoy), t.prioridad))[:3]]
    resumen, consejos, modelo = _con_ia(hechos, "REVISIÓN SEMANAL de pendientes y hábitos. Recomienda qué priorizar.")
    informe = Informe(tipo="revision", periodo=f"{desde:%d/%m} – {hoy:%d/%m/%Y}",
                      hechos={"hechos": hechos, "consejos": consejos, "prioridades": prioridades},
                      texto=resumen, modelo=modelo)
    bd.add(informe)
    bd.commit()
    return informe
