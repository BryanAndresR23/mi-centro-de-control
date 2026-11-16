from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.models import Categoria, Cuenta, Movimiento


def test_crear_tarjeta_con_deuda(cliente_auth, csrf, bd):
    r = cliente_auth.post(
        "/cuentas/nueva",
        data={"csrf_token": csrf, "nombre": "Visa", "tipo": "tarjeta_credito", "saldo_inicial": "250",
              "fecha_saldo_inicial": "2026-01-01", "cupo": "2000", "dia_corte": "20", "dia_pago": "5", "activa": "1"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    visa = bd.scalar(select(Cuenta))
    assert (visa.es_pasivo, visa.saldo_inicial, visa.cupo) == (True, Decimal("250.00"), Decimal("2000.00"))
    texto = cliente_auth.get("/cuentas").text
    assert "disponible $1,750.00" in texto
    assert "Debes $250.00" in texto


def test_deuda_negativa_no_permitida(cliente_auth, csrf):
    r = cliente_auth.post(
        "/cuentas/nueva",
        data={"csrf_token": csrf, "nombre": "Visa", "tipo": "tarjeta_credito", "saldo_inicial": "-10",
              "fecha_saldo_inicial": "2026-01-01"},
    )
    assert r.status_code == 422


def test_no_se_elimina_cuenta_con_movimientos(cliente_auth, csrf, bd):
    c = Cuenta(nombre="Banco", tipo="ahorros", saldo_inicial=Decimal("0"), fecha_saldo_inicial=date(2026, 1, 1))
    bd.add(c)
    bd.commit()
    bd.add(Movimiento(fecha=date(2026, 1, 2), tipo="ingreso", monto=Decimal("5"), cuenta_id=c.id))
    bd.commit()
    cliente_auth.post(f"/cuentas/{c.id}/eliminar", data={"csrf_token": csrf})
    bd.expunge_all()
    assert bd.get(Cuenta, c.id) is not None
    # Tampoco puede pasar de activo a pasivo
    r = cliente_auth.post(
        f"/cuentas/{c.id}/editar",
        data={"csrf_token": csrf, "nombre": "Banco", "tipo": "prestamo", "fecha_saldo_inicial": "2026-01-01", "activa": "1"},
    )
    assert r.status_code == 422


def test_cuenta_sin_movimientos_se_elimina(cliente_auth, csrf, bd):
    c = Cuenta(nombre="Vieja", tipo="efectivo", saldo_inicial=Decimal("0"), fecha_saldo_inicial=date(2026, 1, 1))
    bd.add(c)
    bd.commit()
    cuenta_id = c.id
    cliente_auth.post(f"/cuentas/{cuenta_id}/eliminar", data={"csrf_token": csrf})
    bd.expunge_all()
    assert bd.get(Cuenta, cuenta_id) is None


def test_subcategoria_y_reglas(cliente_auth, csrf, bd):
    cliente_auth.get("/categorias")  # crea las categorías base
    comida = bd.scalar(select(Categoria).where(Categoria.nombre == "Comida fuera"))
    sueldo = bd.scalar(select(Categoria).where(Categoria.nombre == "Sueldo"))

    r = cliente_auth.post(
        "/categorias/nueva",
        data={"csrf_token": csrf, "nombre": "Almuerzos de oficina", "tipo": "gasto", "padre_id": comida.id,
              "rubro_sri": "alimentacion", "activa": "1"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    nueva = bd.scalar(select(Categoria).where(Categoria.nombre == "Almuerzos de oficina"))
    assert nueva.padre_id == comida.id

    # La categoría padre debe ser del mismo tipo
    r = cliente_auth.post(
        "/categorias/nueva",
        data={"csrf_token": csrf, "nombre": "Mal", "tipo": "gasto", "padre_id": sueldo.id, "activa": "1"},
    )
    assert r.status_code == 422

    # Un ingreso no lleva rubro SRI aunque se envíe
    cliente_auth.post(
        "/categorias/nueva",
        data={"csrf_token": csrf, "nombre": "Bono", "tipo": "ingreso", "rubro_sri": "salud", "activa": "1"},
    )
    assert bd.scalar(select(Categoria).where(Categoria.nombre == "Bono")).rubro_sri is None


def test_categorias_base_no_reaparecen(cliente_auth, csrf, bd):
    cliente_auth.get("/categorias")
    total = len(bd.scalars(select(Categoria)).all())
    otra = bd.scalar(select(Categoria).where(Categoria.nombre == "Regalos"))
    cliente_auth.post(f"/categorias/{otra.id}/eliminar", data={"csrf_token": csrf})
    cliente_auth.get("/categorias")
    assert len(bd.scalars(select(Categoria)).all()) == total - 1
