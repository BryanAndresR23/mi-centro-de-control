from datetime import date, timedelta
from decimal import Decimal as D

from sqlalchemy import func, select

from app import ia, notificaciones
from app.asesor import hechos as h
from app.asesor.informe import generar_financiero, generar_revision
from app.demo import cargar_demo
from app.finanzas.calculos import Mov
from app.models import AvisoEnviado, Informe

HOY = date(2026, 11, 16)


def test_comparar_categorias_detecta_subidas():
    movs = [Mov(HOY - timedelta(days=d), "gasto", D("10"), 1, categoria_id=7) for d in range(1, 31)]  # $10 diarios base
    movs += [Mov(HOY - timedelta(days=d), "gasto", D("20"), 1, categoria_id=7) for d in range(31, 38)]  # semana actual $20/día
    periodo = (HOY - timedelta(days=37), HOY - timedelta(days=31))
    base = (HOY - timedelta(days=30), HOY - timedelta(days=1))
    [hecho] = h.comparar_categorias(movs, periodo, base, {}, {7: "Comida fuera"})
    assert hecho["tipo"] == "categoria_sube" and "100% más en Comida fuera" in hecho["texto"]


def test_gastos_hormiga_y_suscripciones():
    movs = [h.MovDetalle(HOY - timedelta(days=d), "gasto", D("3"), 1, comercio="Café") for d in range(1, 9)]
    hecho = h.gastos_hormiga(movs, (HOY - timedelta(days=10), HOY))
    assert hecho and "8 compras" in hecho["texto"]
    assert h.gastos_hormiga(movs[:3], (HOY - timedelta(days=10), HOY)) is None
    subs = h.suscripciones([("Netflix", D("9.99"), 3), ("Spotify", D("5.99"), 0)])
    assert any(x["tipo"] == "suscripcion_sin_uso" and "Netflix" in x["texto"] for x in subs)


def test_metas_y_recortes_calcula_meses_antes():
    movs = [Mov(HOY - timedelta(days=d), "gasto", D("10"), 1, categoria_id=7) for d in range(1, 90)]  # ~$300/mes
    [hecho] = h.metas_y_recortes([("Maestría", D("3000"), D("1000"), D("200"), None, 10)], movs, HOY, {}, {7: "Comida fuera"})
    assert "llegas en 8 meses (2 antes)" in hecho["texto"]


def test_informe_sin_ia_usa_hechos(bd, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cargar_demo(bd, HOY)
    informe = generar_financiero(bd, "mensual", HOY)
    assert informe.modelo is None and informe.hechos["hechos"]
    assert all(c["hechos"][0].startswith("h") for c in informe.hechos["consejos"])
    revision = generar_revision(bd, HOY)
    assert revision.tipo == "revision" and revision.hechos["prioridades"]


def test_informe_con_ia_descarta_consejos_sin_hechos_validos(bd, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "prueba")
    respuesta = {"resumen": "Buen mes.", "consejos": [
        {"titulo": "Válido", "detalle": "Basado en h1", "hechos": ["h1"], "ahorro_mensual_usd": 20},
        {"titulo": "Inventado", "detalle": "Sin base", "hechos": ["h999"], "ahorro_mensual_usd": None},
    ]}
    monkeypatch.setattr(ia, "pedir_json", lambda *a, **k: (respuesta, "claude-opus-5-5"))
    cargar_demo(bd, HOY)
    informe = generar_financiero(bd, "semanal", HOY)
    assert informe.texto == "Buen mes." and informe.modelo == "claude-opus-5-5"
    assert [c["titulo"] for c in informe.hechos["consejos"]] == ["Válido"]


def test_cron_protegido_y_sin_repeticiones(cliente, bd, monkeypatch):
    monkeypatch.delenv("CRON_SECRET", raising=False)
    assert cliente.get("/cron/diario").status_code == 503
    monkeypatch.setenv("CRON_SECRET", "secreto-de-prueba")
    assert cliente.get("/cron/diario").status_code == 401
    assert cliente.get("/cron/diario", headers={"Authorization": "Bearer otro"}).status_code == 401
    enviados = []
    monkeypatch.setattr(notificaciones, "enviar", lambda texto: enviados.append(texto) or True)
    cargar_demo(bd, date.today())
    r = cliente.get("/cron/diario", headers={"Authorization": "Bearer secreto-de-prueba"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert enviados and "Pagos" in enviados[0]
    r2 = cliente.get("/cron/diario", headers={"Authorization": "Bearer secreto-de-prueba"})
    assert r2.json().get("repetido") is True and len(enviados) == 1
    assert bd.scalar(select(func.count()).select_from(AvisoEnviado)) >= 1


def test_paginas_del_asesor(cliente_auth, csrf, bd, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cargar_demo(bd, date.today())
    assert cliente_auth.get("/asesor").status_code == 200
    r = cliente_auth.post("/asesor/generar/semanal", data={"csrf_token": csrf}, follow_redirects=False)
    assert r.status_code == 303
    assert cliente_auth.get(r.headers["location"]).status_code == 200
    assert bd.scalar(select(func.count()).select_from(Informe)) == 1
