import io
import json
import zipfile
from datetime import date

from sqlalchemy import func, select

from app.demo import cargar_demo
from app.finanzas.calculos import patrimonio_neto
from app.finanzas.servicio import cuentas_para_calculo, listar_cuentas, movs_para_calculo
from app.models import Categoria, Cuenta, Movimiento
from app.respaldo import FORMATO, exportar, leer_archivo, restaurar

HOY = date(2026, 11, 16)


def contar(bd, modelo):
    return bd.scalar(select(func.count()).select_from(modelo))


def patrimonio(bd):
    return patrimonio_neto(cuentas_para_calculo(listar_cuentas(bd)), movs_para_calculo(bd), HOY)


def test_exportar_y_restaurar_deja_todo_igual(bd):
    cargar_demo(bd, HOY)
    # Una subcategoría, para probar la referencia a la misma tabla
    padre = bd.scalar(select(Categoria).where(Categoria.nombre == "Comida fuera"))
    bd.add(Categoria(nombre="Almuerzos", tipo="gasto", padre_id=padre.id))
    bd.commit()
    antes = (contar(bd, Cuenta), contar(bd, Categoria), contar(bd, Movimiento), patrimonio(bd))

    datos = exportar(bd)
    bd.query(Movimiento).delete()
    bd.commit()
    assert contar(bd, Movimiento) == 0

    restaurar(bd, json.loads(json.dumps(datos)))  # pasa por JSON, como un archivo real
    despues = (contar(bd, Cuenta), contar(bd, Categoria), contar(bd, Movimiento), patrimonio(bd))
    assert despues == antes
    sub = bd.scalar(select(Categoria).where(Categoria.nombre == "Almuerzos"))
    assert sub.padre_id == padre.id


def test_descarga_zip_con_json_y_csv(cliente_auth, bd):
    cargar_demo(bd, HOY)
    r = cliente_auth.get("/respaldo/descargar")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        nombres = set(z.namelist())
        assert {"datos.json", "movimientos.csv", "cuentas.csv", "categorias.csv", "LEEME.txt"} <= nombres
        assert "intentos_login.csv" not in nombres
        datos = json.loads(z.read("datos.json"))
        assert datos["formato"] == FORMATO
        primera_linea = z.read("movimientos.csv").decode("utf-8-sig").splitlines()[0]
        assert primera_linea.startswith("id,fecha,tipo,monto")


def test_restaurar_desde_la_web_exige_confirmacion(cliente_auth, csrf, bd):
    cargar_demo(bd, HOY)
    contenido = cliente_auth.get("/respaldo/descargar").content
    archivo = {"archivo": ("respaldo.zip", contenido, "application/zip")}

    r = cliente_auth.post("/respaldo/restaurar", data={"csrf_token": csrf, "confirmacion": "no"}, files=archivo)
    assert r.status_code == 422
    assert "REEMPLAZAR" in r.text

    r = cliente_auth.post(
        "/respaldo/restaurar", data={"csrf_token": csrf, "confirmacion": "REEMPLAZAR"}, files=archivo, follow_redirects=False
    )
    assert r.status_code == 303


def test_archivo_invalido_no_borra_nada(cliente_auth, csrf, bd):
    cargar_demo(bd, HOY)
    antes = contar(bd, Movimiento)
    for contenido in (b"no es json", json.dumps({"formato": "otra-app"}).encode(), b"PK\x03\x04roto"):
        r = cliente_auth.post(
            "/respaldo/restaurar",
            data={"csrf_token": csrf, "confirmacion": "REEMPLAZAR"},
            files={"archivo": ("x.json", contenido, "application/json")},
        )
        assert r.status_code == 422
    assert contar(bd, Movimiento) == antes


def test_respaldo_inconsistente_se_revierte(bd):
    cargar_demo(bd, HOY)
    antes = contar(bd, Movimiento)
    datos = exportar(bd)
    datos["tablas"]["movimientos"][0]["cuenta_id"] = 99999  # cuenta inexistente
    try:
        restaurar(bd, datos)
    except Exception:
        pass
    else:
        raise AssertionError("Debió fallar por la clave foránea")
    assert contar(bd, Movimiento) == antes


def test_leer_archivo_rechaza_version_futura():
    contenido = json.dumps({"formato": FORMATO, "version": 99, "tablas": {}}).encode()
    try:
        leer_archivo(contenido)
    except ValueError as exc:
        assert "versión" in str(exc)
    else:
        raise AssertionError("Debió rechazar la versión")


def test_datos_demo_son_coherentes(bd):
    resumen = cargar_demo(bd, HOY)
    assert resumen["movimientos"] > 300
    from app.finanzas.calculos import saldos_al

    cuentas = listar_cuentas(bd)
    saldos = saldos_al(cuentas_para_calculo(cuentas), movs_para_calculo(bd), HOY)
    for c in cuentas:
        if c.es_pasivo:
            assert saldos[c.id] <= 0, c.nombre  # la tarjeta nunca queda a favor
            assert -saldos[c.id] < c.cupo, c.nombre
        else:
            assert saldos[c.id] >= 0, c.nombre  # ninguna cuenta en rojo
