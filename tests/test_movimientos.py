from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.models import Categoria, Cuenta, Movimiento
from app.tiempo import hoy


def crear_cuentas(bd):
    banco = Cuenta(nombre="Banco", tipo="ahorros", saldo_inicial=Decimal("500"), fecha_saldo_inicial=date(2026, 1, 1))
    ahorro = Cuenta(nombre="Ahorro", tipo="ahorros", saldo_inicial=Decimal("0"), fecha_saldo_inicial=date(2026, 1, 1))
    visa = Cuenta(nombre="Visa", tipo="tarjeta_credito", saldo_inicial=Decimal("0"), fecha_saldo_inicial=date(2026, 1, 1))
    bd.add_all([banco, ahorro, visa])
    bd.commit()
    return banco, ahorro, visa


def categoria(bd, nombre: str) -> Categoria:
    return bd.scalar(select(Categoria).where(Categoria.nombre == nombre))


def test_formulario_nuevo_crea_categorias_base(cliente_auth, bd):
    r = cliente_auth.get("/movimientos/nuevo")
    assert r.status_code == 200
    assert categoria(bd, "Supermercado") is not None
    assert categoria(bd, "Supermercado").rubro_sri == "alimentacion"


def test_registrar_gasto(cliente_auth, csrf, bd):
    banco, _, _ = crear_cuentas(bd)
    cliente_auth.get("/movimientos/nuevo")
    super_ = categoria(bd, "Supermercado")
    r = cliente_auth.post(
        "/movimientos/nuevo",
        data={
            "csrf_token": csrf, "tipo": "gasto", "monto": "45,30", "fecha": hoy().isoformat(),
            "cuenta_id": banco.id, "categoria_gasto": super_.id, "comercio": "Supermaxi", "deducible": "1",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    mov = bd.scalar(select(Movimiento))
    assert (mov.tipo, mov.monto, mov.comercio, mov.deducible) == ("gasto", Decimal("45.30"), "Supermaxi", True)
    # Aparece en la lista y baja el saldo de la cuenta
    assert "Supermaxi" in cliente_auth.get("/movimientos").text
    assert "$454.70" in cliente_auth.get("/cuentas").text


def test_ahorro_se_guarda_como_transferencia(cliente_auth, csrf, bd):
    banco, ahorro, _ = crear_cuentas(bd)
    r = cliente_auth.post(
        "/movimientos/nuevo",
        data={
            "csrf_token": csrf, "tipo": "ahorro", "monto": "200", "fecha": hoy().isoformat(),
            "cuenta_id": banco.id, "cuenta_destino_ahorro": ahorro.id,
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    mov = bd.scalar(select(Movimiento))
    assert (mov.tipo, mov.cuenta_destino_id, mov.categoria_id) == ("transferencia", ahorro.id, None)


def test_validaciones(cliente_auth, csrf, bd):
    banco, _, visa = crear_cuentas(bd)
    casos = [
        ({"tipo": "gasto", "monto": "0", "cuenta_id": banco.id}, "mayor que cero"),
        ({"tipo": "gasto", "monto": "abc", "cuenta_id": banco.id}, "No entiendo el monto"),
        ({"tipo": "gasto", "monto": "10", "cuenta_id": banco.id}, "Elige una categoría"),
        ({"tipo": "transferencia", "monto": "10", "cuenta_id": banco.id, "cuenta_destino_id": banco.id}, "distinta"),
        ({"tipo": "ahorro", "monto": "10", "cuenta_id": banco.id, "cuenta_destino_ahorro": visa.id}, "ahorros o inversión"),
        ({"tipo": "gasto", "monto": "10", "cuenta_id": banco.id, "clave_acceso": "123"}, "49 dígitos"),
        ({"tipo": "ingreso", "monto": "10", "cuenta_id": "999"}, "Elige una cuenta"),
    ]
    for datos, mensaje in casos:
        datos = {"csrf_token": csrf, "fecha": hoy().isoformat(), **datos}
        r = cliente_auth.post("/movimientos/nuevo", data=datos)
        assert r.status_code == 422, datos
        assert mensaje in r.text, (datos, mensaje)
    assert bd.scalar(select(Movimiento)) is None


def test_fecha_muy_lejana_en_el_futuro(cliente_auth, csrf, bd):
    banco, ahorro, _ = crear_cuentas(bd)
    lejana = (hoy() + timedelta(days=400)).isoformat()
    r = cliente_auth.post(
        "/movimientos/nuevo",
        data={"csrf_token": csrf, "tipo": "transferencia", "monto": "5", "fecha": lejana,
              "cuenta_id": banco.id, "cuenta_destino_id": ahorro.id},
    )
    assert r.status_code == 422


def test_clave_de_acceso_duplicada(cliente_auth, csrf, bd):
    banco, _, _ = crear_cuentas(bd)
    cliente_auth.get("/movimientos/nuevo")
    cat = categoria(bd, "Salud")
    clave = "1" * 49
    datos = {"csrf_token": csrf, "tipo": "gasto", "monto": "10", "fecha": hoy().isoformat(),
             "cuenta_id": banco.id, "categoria_gasto": cat.id, "clave_acceso": clave}
    assert cliente_auth.post("/movimientos/nuevo", data=datos, follow_redirects=False).status_code == 303
    r = cliente_auth.post("/movimientos/nuevo", data=datos)
    assert r.status_code == 422
    assert "ya está registrada" in r.text


def test_editar_y_eliminar(cliente_auth, csrf, bd):
    banco, _, _ = crear_cuentas(bd)
    cliente_auth.get("/movimientos/nuevo")
    sueldo = categoria(bd, "Sueldo")
    mov = Movimiento(fecha=hoy(), tipo="ingreso", monto=Decimal("1000"), cuenta_id=banco.id, categoria_id=sueldo.id)
    bd.add(mov)
    bd.commit()
    mov_id = mov.id

    assert cliente_auth.get(f"/movimientos/{mov_id}/editar").status_code == 200
    r = cliente_auth.post(
        f"/movimientos/{mov_id}/editar",
        data={"csrf_token": csrf, "tipo": "ingreso", "monto": "1,250.00", "fecha": hoy().isoformat(),
              "cuenta_id": banco.id, "categoria_ingreso": sueldo.id},
        follow_redirects=False,
    )
    assert r.status_code == 303
    bd.expunge_all()
    assert bd.get(Movimiento, mov_id).monto == Decimal("1250.00")

    r = cliente_auth.post(f"/movimientos/{mov_id}/eliminar", data={"csrf_token": csrf}, follow_redirects=False)
    assert r.status_code == 303
    bd.expunge_all()
    assert bd.get(Movimiento, mov_id) is None
    assert cliente_auth.get(f"/movimientos/{mov_id}/editar").status_code == 404


def test_filtros_de_la_lista(cliente_auth, bd):
    banco, _, visa = crear_cuentas(bd)
    cliente_auth.get("/movimientos/nuevo")
    cafe, cine = categoria(bd, "Café y snacks"), categoria(bd, "Entretenimiento")
    bd.add_all([
        Movimiento(fecha=hoy(), tipo="gasto", monto=Decimal("3"), cuenta_id=banco.id, categoria_id=cafe.id, comercio="Juan Valdez"),
        Movimiento(fecha=hoy(), tipo="gasto", monto=Decimal("12"), cuenta_id=visa.id, categoria_id=cine.id, comercio="Supercines"),
    ])
    bd.commit()
    mes = hoy().strftime("%Y-%m")
    texto = cliente_auth.get(f"/movimientos?mes={mes}&cuenta={visa.id}").text
    assert "Supercines" in texto and "Juan Valdez" not in texto
    texto = cliente_auth.get(f"/movimientos?mes={mes}&q=valdez").text
    assert "Juan Valdez" in texto and "Supercines" not in texto
    # HTMX recibe solo el fragmento de la lista
    parcial = cliente_auth.get(f"/movimientos?mes={mes}&categoria={cine.id}", headers={"HX-Request": "true"})
    assert "Supercines" in parcial.text and "<html" not in parcial.text


def test_dashboard_con_datos(cliente_auth, bd):
    from app.demo import cargar_demo

    cargar_demo(bd, hoy())
    r = cliente_auth.get("/")
    assert r.status_code == 200
    assert "Gasto por categoría" in r.text
    assert 'id="datos-graficos"' in r.text
