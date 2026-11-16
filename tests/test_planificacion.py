from datetime import date
from decimal import Decimal as D

from sqlalchemy import select

from app.finanzas.calculos import Mov
from app.finanzas.planificacion import (
    PresupuestoDato,
    aporte_a_meta,
    costo_mensual,
    estado_presupuestos,
    nivel_presupuesto,
    progreso_meta,
    proximos_pagos,
    siguiente_fecha,
    umbral_cruzado,
)
from app.models import Categoria, Cuenta, MetaAhorro, Movimiento, Presupuesto, Recurrente
from app.tiempo import hoy

PADRES = {1: None, 2: 1, 3: None}  # 2 es subcategoría de 1


def test_estado_presupuesto_suma_subcategorias_y_proyecta():
    p = PresupuestoDato(10, 1, D("100"), date(2026, 1, 1))
    movs = [Mov(date(2026, 11, 3), "gasto", D("30"), 1, categoria_id=1),
            Mov(date(2026, 11, 5), "gasto", D("20"), 1, categoria_id=2),
            Mov(date(2026, 11, 6), "gasto", D("99"), 1, categoria_id=3)]
    [e] = estado_presupuestos([p], movs, 2026, 11, PADRES, hoy=date(2026, 11, 10))
    assert (e.gastado, e.pct, e.nivel, e.restante) == (D("50"), D("0.5000"), "ok", D("50"))
    assert e.proyectado == D("150.00")  # 50 en 10 días → 150 en 30


def test_presupuesto_fuera_de_vigencia_no_cuenta():
    viejo = PresupuestoDato(1, 1, D("100"), date(2026, 1, 1), hasta_mes=date(2026, 9, 1))
    assert estado_presupuestos([viejo], [], 2026, 11, PADRES, hoy=date(2026, 11, 10)) == []
    assert len(estado_presupuestos([viejo], [], 2026, 9, PADRES, hoy=date(2026, 11, 10))) == 1


def test_niveles_y_umbrales():
    assert nivel_presupuesto(D("0.79")) == "ok"
    assert nivel_presupuesto(D("0.80")) == "alerta"
    assert nivel_presupuesto(D("1.00")) == "excedido"
    assert umbral_cruzado(D("70"), D("85"), D("100")) == D("0.80")
    assert umbral_cruzado(D("85"), D("105"), D("100")) == D("1.00")
    assert umbral_cruzado(D("70"), D("120"), D("100")) == D("1.00")  # cruza ambos: avisa el mayor
    assert umbral_cruzado(D("85"), D("90"), D("100")) is None


def test_siguiente_fecha_respeta_fin_de_mes_y_bisiestos():
    assert siguiente_fecha(date(2027, 1, 31), "mensual", 31) == date(2027, 2, 28)
    assert siguiente_fecha(date(2027, 2, 28), "mensual", 31) == date(2027, 3, 31)
    assert siguiente_fecha(date(2028, 2, 29), "anual", 29) == date(2029, 2, 28)
    assert siguiente_fecha(date(2026, 11, 16), "semanal", 16) == date(2026, 11, 23)


def test_costo_mensual_y_proximos_pagos():
    assert costo_mensual(D("120"), "anual") == D("10.00")
    assert costo_mensual(D("10"), "semanal") == D("43.33")
    hoy_ = date(2026, 11, 16)
    pagos = proximos_pagos([(1, date(2026, 11, 20), D("5")), (2, date(2026, 11, 14), D("9")), (3, date(2027, 1, 1), D("1"))], hoy_)
    assert [(p.recurrente_id, p.estado) for p in pagos] == [(2, "vencido"), (1, "programado")]


def test_progreso_meta_estima_fecha_y_cuota():
    hoy_ = date(2026, 11, 16)
    aportes = [(date(2026, 9, 30), D("200")), (date(2026, 10, 30), D("200")), (date(2026, 11, 10), D("200"))]
    p = progreso_meta(D("2000"), D("200"), aportes, hoy_, fecha_objetivo=date(2027, 3, 16))
    assert p.total == D("800") and p.faltante == D("1200")
    assert p.ritmo_mensual == D("200.00")
    assert p.meses_estimados == 6 and p.fecha_estimada == date(2027, 5, 16)
    assert p.cuota_necesaria == D("300.00")  # 1200 en 4 meses
    assert p.en_camino is False
    assert progreso_meta(D("100"), D("150"), [], hoy_).cumplida


def test_aporte_a_meta():
    assert aporte_a_meta("transferencia", D("50")) == D("50")
    assert aporte_a_meta("gasto", D("20")) == D("-20")


# --- Pantallas -----------------------------------------------------------------


def _base(cliente_auth, bd):
    cliente_auth.get("/movimientos/nuevo")  # crea categorías base
    banco = Cuenta(nombre="Banco", tipo="ahorros", saldo_inicial=D("1000"), fecha_saldo_inicial=date(2026, 1, 1))
    bd.add(banco)
    bd.commit()
    return banco, bd.scalar(select(Categoria).where(Categoria.nombre == "Comida fuera"))


def test_presupuesto_crear_y_no_duplicar(cliente_auth, csrf, bd):
    _, comida = _base(cliente_auth, bd)
    datos = {"csrf_token": csrf, "categoria_id": comida.id, "monto": "100", "desde_mes": f"{hoy():%Y-%m}"}
    assert cliente_auth.post("/presupuestos/nuevo", data=datos, follow_redirects=False).status_code == 303
    r = cliente_auth.post("/presupuestos/nuevo", data=datos)
    assert r.status_code == 422 and "ya tiene un presupuesto" in r.text
    assert "Comida fuera" in cliente_auth.get("/presupuestos").text


def test_alerta_al_cruzar_el_80_por_ciento(cliente_auth, csrf, bd):
    banco, comida = _base(cliente_auth, bd)
    bd.add(Presupuesto(categoria_id=comida.id, monto=D("100"), desde_mes=hoy().replace(day=1)))
    bd.commit()
    base = {"csrf_token": csrf, "tipo": "gasto", "fecha": hoy().isoformat(), "cuenta_id": banco.id, "categoria_gasto": comida.id}
    cliente_auth.post("/movimientos/nuevo", data={**base, "monto": "50"})
    texto = cliente_auth.post("/movimientos/nuevo", data={**base, "monto": "35"}).text
    assert "85 % del presupuesto de Comida fuera" in texto
    texto = cliente_auth.post("/movimientos/nuevo", data={**base, "monto": "20"}).text
    assert "Te pasaste del presupuesto de Comida fuera" in texto


def test_pagar_recurrente_registra_y_avanza(cliente_auth, csrf, bd):
    banco, _ = _base(cliente_auth, bd)
    subs = bd.scalar(select(Categoria).where(Categoria.nombre == "Suscripciones"))
    r = Recurrente(nombre="Netflix", tipo="suscripcion", monto=D("9.99"), frecuencia="mensual",
                   proxima_fecha=date(2027, 1, 31), dia=31, cuenta_id=banco.id, categoria_id=subs.id)
    bd.add(r)
    bd.commit()
    rid = r.id
    assert cliente_auth.post(f"/recurrentes/{rid}/pagar", data={"csrf_token": csrf}, follow_redirects=False).status_code == 303
    bd.expunge_all()
    mov = bd.scalar(select(Movimiento).where(Movimiento.recurrente_id == rid))
    assert (mov.tipo, mov.monto, mov.categoria_id, mov.origen) == ("gasto", D("9.99"), subs.id, "recurrente")
    assert bd.get(Recurrente, rid).proxima_fecha == date(2027, 2, 28)


def test_pago_de_deuda_es_transferencia(cliente_auth, csrf, bd):
    banco, _ = _base(cliente_auth, bd)
    visa = Cuenta(nombre="Visa", tipo="tarjeta_credito", saldo_inicial=D("300"), fecha_saldo_inicial=date(2026, 1, 1))
    bd.add(visa)
    bd.commit()
    datos = {"csrf_token": csrf, "nombre": "Pago Visa", "tipo": "deuda", "monto": "300", "frecuencia": "mensual",
             "proxima_fecha": hoy().isoformat(), "cuenta_id": banco.id, "cuenta_destino_id": visa.id, "dias_aviso": "3", "activa": "1"}
    assert cliente_auth.post("/recurrentes/nuevo", data=datos, follow_redirects=False).status_code == 303
    rid = bd.scalar(select(Recurrente.id))
    cliente_auth.post(f"/recurrentes/{rid}/pagar", data={"csrf_token": csrf})
    mov = bd.scalar(select(Movimiento).where(Movimiento.recurrente_id == rid))
    assert (mov.tipo, mov.cuenta_destino_id, mov.categoria_id) == ("transferencia", visa.id, None)


def test_ahorro_asignado_a_meta_suma_progreso(cliente_auth, csrf, bd):
    banco, _ = _base(cliente_auth, bd)
    ahorro = Cuenta(nombre="Ahorro", tipo="ahorros", saldo_inicial=D("0"), fecha_saldo_inicial=date(2026, 1, 1))
    meta = MetaAhorro(nombre="Viaje", monto_objetivo=D("500"))
    bd.add_all([ahorro, meta])
    bd.commit()
    cliente_auth.post("/movimientos/nuevo", data={"csrf_token": csrf, "tipo": "ahorro", "monto": "125", "fecha": hoy().isoformat(),
                                                  "cuenta_id": banco.id, "cuenta_destino_ahorro": ahorro.id, "meta_id": meta.id})
    texto = cliente_auth.get("/metas").text
    assert "$125.00" in texto and "25%" in texto
