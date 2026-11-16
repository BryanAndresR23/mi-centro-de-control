from datetime import date, timedelta
from decimal import Decimal as D

from sqlalchemy import select

from app.models import Area, Beca, Postulacion, PostulacionEvento, SesionEstudio, Tarea
from app.pendientes import logica
from app.tiempo import hoy

HOY = date(2026, 11, 16)  # lunes


def test_urgencia_y_orden_de_la_vista_hoy():
    T = logica.TareaDato
    tareas = [T(1, None, 1), T(2, HOY + timedelta(days=3), 1), T(3, HOY, 3), T(4, HOY - timedelta(days=2), 2),
              T(5, HOY, 1), T(6, HOY + timedelta(days=1), 2), T(7, HOY, 1, "hecha")]
    assert [t.id for t in logica.ordenar_tareas(tareas, HOY)] == [4, 5, 3, 6, 2, 1]
    assert logica.urgencia(HOY + timedelta(days=30), HOY) == 4


def test_siguiente_ocurrencia():
    assert logica.siguiente_ocurrencia(HOY, "diaria") == HOY + timedelta(days=1)
    assert logica.siguiente_ocurrencia(date(2027, 1, 31), "mensual") == date(2027, 2, 28)
    assert logica.siguiente_ocurrencia(HOY, None) is None


def test_racha_y_minutos_de_la_semana():
    dias = {HOY - timedelta(days=i) for i in (1, 2, 3, 5)}
    assert logica.racha(dias, HOY) == 3  # hoy aún no estudia: la racha de ayer sigue viva
    assert logica.racha(dias | {HOY}, HOY) == 4
    sesiones = [(HOY, 30), (HOY - timedelta(days=1), 45), (HOY - timedelta(days=3), 60)]
    assert logica.minutos_semana(sesiones, date(2026, 11, 18)) == 30  # semana del lunes 16


def test_marcas_de_fuerza():
    assert logica.uno_rm(D("100"), 10) == D("133.3")
    marcas = logica.mejores_marcas([(HOY, "Sentadilla", 8, D("80")), (HOY, "sentadilla", 5, D("90")), (HOY, "Plancha", 1, None)])
    assert marcas["sentadilla"][0] == D("105.0")  # 90 × (1 + 5/30) supera a 80 × (1 + 8/30) = 101.3
    assert "plancha" not in marcas


def test_progreso_y_periodos():
    assert logica.progreso_hitos(["hecha", "pendiente", "cancelada"]) == (1, 2)
    assert logica.periodo_actual(HOY, "trimestral") == "2026-T4"
    assert logica.periodo_actual(HOY, "anual") == "2026"


# --- Pantallas -----------------------------------------------------------------


def test_vista_hoy_crea_areas_y_muestra_tareas(cliente_auth, csrf, bd):
    r = cliente_auth.get("/")
    assert r.status_code == 200
    areas = {a.plantilla: a for a in bd.scalars(select(Area))}
    assert {"general", "fitness", "idioma", "habilidad", "postulaciones", "becas", "metas"} <= set(areas)
    cliente_auth.post("/pendientes/tareas/nueva", data={"csrf_token": csrf, "titulo": "Enviar reporte", "area_id": areas["general"].id,
                                                        "fecha_limite": hoy().isoformat(), "prioridad": "1"})
    assert "Enviar reporte" in cliente_auth.get("/").text


def test_tarea_recurrente_crea_la_siguiente(cliente_auth, csrf, bd):
    cliente_auth.get("/")
    area = bd.scalar(select(Area).where(Area.plantilla == "idioma"))
    cliente_auth.post("/pendientes/tareas/nueva", data={"csrf_token": csrf, "titulo": "Speaking", "area_id": area.id,
                                                        "fecha_limite": hoy().isoformat(), "prioridad": "2"})
    tarea = bd.scalar(select(Tarea))
    tarea.recurrencia = "diaria"
    bd.commit()
    cliente_auth.post(f"/pendientes/tareas/{tarea.id}/hecha", data={"csrf_token": csrf})
    bd.expunge_all()
    tareas = bd.scalars(select(Tarea).order_by(Tarea.id)).all()
    assert [t.estado for t in tareas] == ["hecha", "pendiente"]
    assert tareas[1].fecha_limite == hoy() + timedelta(days=1)


def test_estudio_entrenamiento_postulacion_y_beca(cliente_auth, csrf, bd):
    cliente_auth.get("/")
    areas = {a.plantilla: a for a in bd.scalars(select(Area))}
    cliente_auth.post(f"/pendientes/estudio/{areas['idioma'].id}", data={"csrf_token": csrf, "minutos": "45", "tema": "Listening"})
    assert bd.scalar(select(SesionEstudio)).minutos == 45
    cliente_auth.post("/pendientes/fitness/entrenamiento", data={
        "csrf_token": csrf, "rutina": "Pierna", "ejercicio": ["Sentadilla", "Prensa", ""], "repeticiones": ["8", "12", ""],
        "peso": ["80", "120,5", ""]})
    assert "Sentadilla" in cliente_auth.get(f"/pendientes/area/{areas['fitness'].id}").text
    cliente_auth.post("/pendientes/postulaciones", data={"csrf_token": csrf, "empresa": "ACME", "cargo": "Data Analyst",
                                                         "estado": "postulado"})
    pid = bd.scalar(select(Postulacion.id))
    cliente_auth.post(f"/pendientes/postulaciones/{pid}", data={"csrf_token": csrf, "empresa": "ACME", "cargo": "Data Analyst",
                                                               "estado": "entrevista", "proximo_paso": "Entrevista técnica"})
    assert [e.estado for e in bd.scalars(select(PostulacionEvento).order_by(PostulacionEvento.id))] == ["postulado", "entrevista"]
    cliente_auth.post("/pendientes/becas", data={"csrf_token": csrf, "programa": "MSc Data Science",
                                                 "fecha_limite": (hoy() + timedelta(days=20)).isoformat()})
    beca = bd.scalar(select(Beca))
    assert len(beca.requisitos) == 7
    rid = beca.requisitos[0].id
    cliente_auth.post(f"/pendientes/becas/requisito/{rid}", data={"csrf_token": csrf})
    bd.expunge_all()
    assert bd.get(Beca, beca.id).requisitos[0].hecho is True
    for plantilla in ("idioma", "postulaciones", "becas", "metas", "general"):
        assert cliente_auth.get(f"/pendientes/area/{areas[plantilla].id}").status_code == 200
