from datetime import date
from decimal import Decimal as D

from sqlalchemy import select

from app.models import Categoria, Cuenta, Extraccion, Movimiento
from app.recibos import extraccion as ext
from app.recibos import importar as imp
from app.recibos import sri

HOY = date(2026, 11, 16)


def clave_valida(base48: str = "161120260117900000000011001001000000123123456781") -> str:
    base48 = base48[:48]
    return base48 + str(sri.digito_verificador(base48))


def xml_factura(clave: str, comprador: str = "1712345678") -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<factura id="comprobante" version="1.1.0">
  <infoTributaria><razonSocial>CORPORACION FAVORITA C.A.</razonSocial><nombreComercial>SUPERMAXI</nombreComercial>
    <ruc>1790016919001</ruc><claveAcceso>{clave}</claveAcceso><codDoc>01</codDoc></infoTributaria>
  <infoFactura><fechaEmision>16/11/2026</fechaEmision><identificacionComprador>{comprador}</identificacionComprador>
    <razonSocialComprador>BRYAN</razonSocialComprador><totalSinImpuestos>40.00</totalSinImpuestos>
    <totalConImpuestos><totalImpuesto><codigo>2</codigo><codigoPorcentaje>4</codigoPorcentaje><baseImponible>40.00</baseImponible><valor>6.00</valor></totalImpuesto></totalConImpuestos>
    <propina>0.00</propina><importeTotal>46.00</importeTotal></infoFactura>
  <detalles><detalle><descripcion>LECHE</descripcion></detalle><detalle><descripcion>PAN</descripcion></detalle></detalles>
</factura>"""


def test_clave_de_acceso():
    clave = clave_valida()
    assert sri.clave_valida(clave)
    otra = clave[:48] + str((int(clave[48]) + 1) % 10)
    assert not sri.clave_valida(otra)
    assert not sri.clave_valida("123")
    info = sri.info_clave(clave)
    assert (info["fecha"], info["tipo_documento"], info["ruc"]) == ("2026-11-16", "factura", "1790000000001")


def test_parsear_xml_suelto_y_autorizado():
    clave = clave_valida()
    datos = sri.parsear_xml(xml_factura(clave))
    assert (datos["comercio"], datos["ruc"], datos["fecha"], datos["subtotal"], datos["iva"], datos["total"]) == (
        "SUPERMAXI", "1790016919001", "2026-11-16", "40.00", "6.00", "46.00")
    assert datos["consumidor_final"] is False and datos["items"] == ["LECHE", "PAN"]
    envuelto = f"<autorizacion><estado>AUTORIZADO</estado><comprobante><![CDATA[{xml_factura(clave)}]]></comprobante></autorizacion>"
    assert sri.parsear_xml(envuelto)["autorizado"] is True


def test_xml_invalido():
    for contenido in ("no es xml", "<nota/>"):
        try:
            sri.parsear_xml(contenido)
        except sri.XMLInvalido:
            continue
        raise AssertionError("Debió rechazarse")


def test_normalizar_datos_de_la_foto():
    datos = ext.normalizar({"comercio": " KFC ", "fecha": "2026-11-15", "subtotal": "10,00", "iva": "1,50", "total": "13,00",
                            "clave_acceso": "123", "consumidor_final": True}, HOY)
    assert (datos["total"], datos["subtotal"], datos["comercio"], datos["clave_acceso"]) == ("13.00", "10.00", "KFC", None)
    avisos = " ".join(datos["avisos"])
    assert "no coincide" in avisos and "CONSUMIDOR FINAL" in avisos and "dígito verificador" in avisos
    sin_fecha = ext.normalizar({"fecha": "15/11/2026"}, HOY)
    assert sin_fecha["fecha"] == HOY.isoformat() and "fecha" in " ".join(sin_fecha["avisos"])


def test_leer_csv_detectar_columnas_y_normalizar():
    csv_banco = ("Banco Pichincha\nEstado de cuenta\nFecha;Concepto;Débito;Crédito;Saldo\n"
                 "01/11/2026;SUPERMAXI QUITO;45,20;;954,80\n02/11/2026;TRANSFERENCIA RECIBIDA;;1.480,00;2.434,80\n"
                 "02/11/2026;SUPERMAXI QUITO;45,20;;2.389,60\n;TOTAL;90,40;1.480,00;\n").encode("cp1252")
    filas = imp.leer_tabla(csv_banco, "estado.csv")
    encabezado, columnas = imp.detectar_columnas(filas)
    assert encabezado == 2 and columnas["fecha"] == 0 and columnas["descripcion"] == 1
    assert columnas["debito"] == 2 and columnas["credito"] == 3 and columnas["monto"] is None
    normalizadas, descartadas = imp.normalizar_filas(filas, encabezado, columnas)
    assert [n["monto"] for n in normalizadas] == ["-45.20", "1480.00", "-45.20"]
    assert descartadas == 1
    huellas = imp.hashes(normalizadas, cuenta_id=1)
    assert len(set(huellas)) == 3  # dos compras idénticas en días distintos


def test_tarjeta_con_monto_positivo_como_gasto():
    filas = [["Fecha", "Descripción", "Monto"], ["2026-11-03", "UBER", "8.50"], ["2026-11-04", "PAGO RECIBIDO", "-200.00"]]
    encabezado, columnas = imp.detectar_columnas(filas)
    normalizadas, _ = imp.normalizar_filas(filas, encabezado, columnas, positivos_son_gastos=True)
    assert [n["monto"] for n in normalizadas] == ["-8.50", "200.00"]


# --- Pantallas -----------------------------------------------------------------


def _cuenta(cliente_auth, bd):
    cliente_auth.get("/recibos")
    cuenta = Cuenta(nombre="Banco", tipo="ahorros", saldo_inicial=D("0"), fecha_saldo_inicial=date(2026, 1, 1))
    bd.add(cuenta)
    bd.commit()
    return cuenta


def test_subir_xml_y_confirmar(cliente_auth, csrf, bd, monkeypatch):
    monkeypatch.setattr(sri, "consultar_autorizacion", lambda clave, timeout=8.0: None)
    cuenta = _cuenta(cliente_auth, bd)
    clave = clave_valida()
    r = cliente_auth.post("/recibos/subir", data={"csrf_token": csrf},
                          files={"archivos": ("factura.xml", xml_factura(clave).encode(), "text/xml")},
                          headers={"Accept": "application/json"})
    assert r.json()["resultados"][0]["ok"] is True
    e = bd.scalar(select(Extraccion))
    assert e.datos["total"] == "46.00" and e.datos["deducible"] is True  # supermercado → alimentación
    super_ = bd.scalar(select(Categoria).where(Categoria.nombre == "Supermercado"))
    assert e.datos["categoria_id"] == super_.id
    form = {"csrf_token": csrf, "monto": e.datos["total"], "fecha": e.datos["fecha"], "comercio": e.datos["comercio"],
            "categoria_gasto": super_.id, "cuenta_id": cuenta.id, "clave_acceso": clave, "ruc_emisor": e.datos["ruc"], "deducible": "1"}
    assert cliente_auth.post(f"/recibos/{e.id}/confirmar", data=form, follow_redirects=False).status_code == 303
    mov = bd.scalar(select(Movimiento))
    assert (mov.monto, mov.clave_acceso, mov.origen, mov.deducible) == (D("46.00"), clave, "xml", True)


def test_foto_sin_clave_de_ia_queda_para_llenar_a_mano(cliente_auth, csrf, bd, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _cuenta(cliente_auth, bd)
    jpeg = b"\xff\xd8\xff\xe0" + b"0" * 100
    r = cliente_auth.post("/recibos/subir", data={"csrf_token": csrf}, files={"archivos": ("r.jpg", jpeg, "image/jpeg")},
                          headers={"Accept": "application/json"})
    assert r.json()["resultados"][0]["ok"] is False
    e = bd.scalar(select(Extraccion))
    assert e.estado == "error" and "ANTHROPIC_API_KEY" in e.error
    assert cliente_auth.get(f"/recibos/{e.id}/archivo").content == jpeg


def test_foto_con_ia_simulada_y_regla_aprendida(cliente_auth, csrf, bd, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "prueba")
    respuesta = {"comercio": "Juan Valdez Café", "ruc": None, "fecha": "2026-11-15", "subtotal": None, "iva": None,
                 "total": "4.50", "clave_acceso": None, "tipo_documento": "ticket", "consumidor_final": None,
                 "categoria_sugerida": "Comida fuera", "confianza": 0.9, "observaciones": None}
    monkeypatch.setattr(ext, "extraer_de_imagen", lambda contenido, tipo, categorias: (respuesta, "claude-opus-5-5"))
    cuenta = _cuenta(cliente_auth, bd)
    jpeg = b"\xff\xd8\xff\xe0" + b"1" * 50
    cliente_auth.post("/recibos/subir", data={"csrf_token": csrf}, files={"archivos": ("r.jpg", jpeg, "image/jpeg")})
    e = bd.scalar(select(Extraccion))
    comida = bd.scalar(select(Categoria).where(Categoria.nombre == "Comida fuera"))
    cafe = bd.scalar(select(Categoria).where(Categoria.nombre == "Café y snacks"))
    assert e.estado == "lista" and e.datos["categoria_id"] == comida.id and e.modelo == "claude-opus-5-5"
    # El usuario corrige a «Café y snacks»: la próxima vez se sugiere sola
    cliente_auth.post(f"/recibos/{e.id}/confirmar", data={"csrf_token": csrf, "monto": "4.50", "fecha": "2026-11-15",
                                                          "comercio": "Juan Valdez Café", "categoria_gasto": cafe.id, "cuenta_id": cuenta.id})
    cliente_auth.post("/recibos/subir", data={"csrf_token": csrf}, files={"archivos": ("r2.jpg", jpeg, "image/jpeg")})
    segunda = bd.scalars(select(Extraccion).order_by(Extraccion.id.desc())).first()
    assert segunda.datos["categoria_id"] == cafe.id


def test_importar_csv_sin_duplicar(cliente_auth, csrf, bd):
    cuenta = _cuenta(cliente_auth, bd)
    contenido = b"Fecha,Descripcion,Monto\n2026-11-01,SUPERMAXI,-45.20\n2026-11-02,SUELDO,1480.00\n"
    for intento in range(2):
        r = cliente_auth.post("/importar/analizar", data={"csrf_token": csrf, "cuenta_id": cuenta.id},
                              files={"archivo": ("e.csv", contenido, "text/csv")})
        assert r.status_code == 200 and "SUPERMAXI" in r.text
        adjunto = int(r.text.split('name="adjunto_id" value="')[1].split('"')[0])
        r = cliente_auth.post("/importar/preparar", data={"csrf_token": csrf, "adjunto_id": adjunto, "cuenta_id": cuenta.id,
                                                         "encabezado": "0", "fecha": "0", "descripcion": "1", "monto": "2"},
                              follow_redirects=False)
        lote = r.headers["location"].rsplit("/", 1)[1]
        filas = bd.scalars(select(Extraccion).where(Extraccion.lote == lote)).all()
        if intento == 1:
            assert all(f.datos["duplicado"] for f in filas)
        incluir = [str(f.id) for f in filas if not f.datos["duplicado"]]
        cliente_auth.post(f"/importar/lote/{lote}", data={"csrf_token": csrf, "incluir": incluir})
    movs = bd.scalars(select(Movimiento)).all()
    assert sorted((m.tipo, m.monto) for m in movs) == [("gasto", D("45.20")), ("ingreso", D("1480.00"))]
