"""Todas las páginas cargan con los datos de ejemplo (detecta errores de plantillas)."""

from datetime import date

from sqlalchemy import select

from app.demo import cargar_demo
from app.models import Area, MetaAhorro, Presupuesto, Recurrente, Tarea


def test_todas_las_paginas_cargan(cliente_auth, bd):
    cargar_demo(bd, date.today())
    rutas = ["/", "/finanzas", "/finanzas?mes=2026-01", "/movimientos", "/movimientos/nuevo", "/movimientos/nuevo?tipo=ahorro",
             "/cuentas", "/categorias", "/mas", "/respaldo", "/presupuestos", "/presupuestos/nuevo", "/metas", "/metas/nueva",
             "/recurrentes", "/recurrentes/nuevo", "/recibos", "/importar", "/pendientes", "/pendientes/areas/nueva", "/asesor"]
    rutas += [f"/pendientes/area/{a.id}" for a in bd.scalars(select(Area))]
    rutas += [f"/pendientes/area/{bd.scalar(select(Area.id))}/editar", f"/pendientes/tareas/{bd.scalar(select(Tarea.id))}/editar",
              f"/presupuestos/{bd.scalar(select(Presupuesto.id))}/editar", f"/metas/{bd.scalar(select(MetaAhorro.id))}/editar",
              f"/recurrentes/{bd.scalar(select(Recurrente.id))}/editar"]
    for ruta in rutas:
        r = cliente_auth.get(ruta)
        assert r.status_code == 200, (ruta, r.status_code, r.text[:300])
