"""Facturas electrónicas del SRI: clave de acceso, XML del comprobante y consulta de autorización."""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from datetime import date
from decimal import Decimal, InvalidOperation

from defusedxml import ElementTree as ET

CONSUMIDOR_FINAL = "9999999999999"
TIPOS_COMPROBANTE = {
    "01": "factura",
    "03": "liquidacion_compra",
    "04": "nota_credito",
    "05": "nota_debito",
    "06": "guia_remision",
    "07": "retencion",
}
URL_AUTORIZACION = "https://cel.sri.gob.ec/comprobantes-electronicos-ws/AutorizacionComprobantesOffline"


class XMLInvalido(ValueError):
    pass


def digito_verificador(clave48: str) -> int:
    """Módulo 11 con pesos 2..7 desde la derecha (algoritmo del SRI)."""
    pesos = [2, 3, 4, 5, 6, 7]
    total = sum(int(d) * pesos[i % 6] for i, d in enumerate(reversed(clave48)))
    dv = 11 - total % 11
    return 0 if dv == 11 else 1 if dv == 10 else dv


def clave_valida(clave: str | None) -> bool:
    clave = re.sub(r"\D", "", clave or "")
    return len(clave) == 49 and digito_verificador(clave[:48]) == int(clave[48])


def info_clave(clave: str) -> dict:
    """Datos que trae la propia clave de acceso: fecha, tipo de comprobante y RUC del emisor."""
    clave = re.sub(r"\D", "", clave)
    try:
        fecha = date(int(clave[4:8]), int(clave[2:4]), int(clave[0:2]))
    except ValueError:
        fecha = None
    return {
        "fecha": fecha.isoformat() if fecha else None,
        "tipo_documento": TIPOS_COMPROBANTE.get(clave[8:10], "otro"),
        "ruc": clave[10:23],
        "ambiente": "produccion" if clave[23] == "2" else "pruebas",
    }


def _texto(nodo, ruta: str) -> str | None:
    hallado = nodo.find(ruta) if nodo is not None else None
    return hallado.text.strip() if hallado is not None and hallado.text else None


def _decimal(valor: str | None) -> str | None:
    if not valor:
        return None
    try:
        return str(Decimal(valor.strip()).quantize(Decimal("0.01")))
    except InvalidOperation:
        return None


def _sin_espacios_de_nombre(raiz) -> None:
    for elemento in raiz.iter():
        if isinstance(elemento.tag, str) and "}" in elemento.tag:
            elemento.tag = elemento.tag.split("}", 1)[1]


def parsear_xml(contenido: bytes | str) -> dict:
    """Lee el XML de una factura (suelto o dentro de <autorizacion>) y devuelve los datos normalizados."""
    try:
        raiz = ET.fromstring(contenido if isinstance(contenido, bytes) else contenido.encode("utf-8"))
    except ET.ParseError as exc:
        raise XMLInvalido("El archivo no es un XML válido.") from exc
    _sin_espacios_de_nombre(raiz)

    autorizado = None
    if raiz.tag != "factura":
        comprobante = raiz.find(".//comprobante")
        if comprobante is not None and comprobante.text and comprobante.text.strip().startswith("<"):
            autorizado = (_texto(raiz, ".//estado") or "").upper() == "AUTORIZADO"
            return {**parsear_xml(comprobante.text.strip()), "autorizado": autorizado,
                    "fecha_autorizacion": _texto(raiz, ".//fechaAutorizacion")}
        factura = raiz.find(".//factura")
        if factura is None:
            raise XMLInvalido("El XML no contiene una factura electrónica.")
        raiz = factura

    info = raiz.find("infoTributaria")
    detalle = raiz.find("infoFactura")
    if info is None or detalle is None:
        raise XMLInvalido("Faltan los bloques infoTributaria/infoFactura.")

    fecha_txt = _texto(detalle, "fechaEmision")  # dd/mm/aaaa
    fecha = None
    if fecha_txt and re.fullmatch(r"\d{2}/\d{2}/\d{4}", fecha_txt):
        d, m, a = fecha_txt.split("/")
        fecha = f"{a}-{m}-{d}"

    iva = Decimal("0")
    for impuesto in detalle.findall("totalConImpuestos/totalImpuesto"):
        if _texto(impuesto, "codigo") == "2":  # 2 = IVA
            iva += Decimal(_decimal(_texto(impuesto, "valor")) or "0")

    comprador = _texto(detalle, "identificacionComprador")
    items = [(_texto(d, "descripcion") or "").strip() for d in raiz.findall("detalles/detalle")]
    return {
        "comercio": _texto(info, "nombreComercial") or _texto(info, "razonSocial"),
        "razon_social": _texto(info, "razonSocial"),
        "ruc": _texto(info, "ruc"),
        "clave_acceso": _texto(info, "claveAcceso"),
        "tipo_documento": TIPOS_COMPROBANTE.get(_texto(info, "codDoc") or "", "otro"),
        "fecha": fecha,
        "subtotal": _decimal(_texto(detalle, "totalSinImpuestos")),
        "iva": str(iva.quantize(Decimal("0.01"))),
        "propina": _decimal(_texto(detalle, "propina")),
        "total": _decimal(_texto(detalle, "importeTotal")),
        "comprador": _texto(detalle, "razonSocialComprador"),
        "identificacion_comprador": comprador,
        "consumidor_final": comprador == CONSUMIDOR_FINAL if comprador else None,
        "items": [i for i in items if i][:15],
        "autorizado": autorizado,
    }


def consultar_autorizacion(clave: str, timeout: float = 8.0) -> str | None:
    """Pide al SRI el XML autorizado de una clave de acceso. None si no responde o no existe."""
    solo_digitos = re.sub(r"\D", "", clave)
    sobre = (
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:ec="http://ec.gob.sri.ws.autorizacion"><soapenv:Header/><soapenv:Body>'
        f"<ec:autorizacionComprobante><claveAccesoComprobante>{solo_digitos}"
        "</claveAccesoComprobante></ec:autorizacionComprobante></soapenv:Body></soapenv:Envelope>"
    )
    pedido = urllib.request.Request(
        URL_AUTORIZACION, data=sobre.encode("utf-8"), method="POST",
        headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": ""},
    )
    try:
        with urllib.request.urlopen(pedido, timeout=timeout) as respuesta:
            cuerpo = respuesta.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
    try:
        raiz = ET.fromstring(cuerpo)
    except ET.ParseError:
        return None
    _sin_espacios_de_nombre(raiz)
    for autorizacion in raiz.iter("autorizacion"):
        if (_texto(autorizacion, "estado") or "").upper() == "AUTORIZADO":
            comprobante = _texto(autorizacion, "comprobante")
            if comprobante:
                fecha = _texto(autorizacion, "fechaAutorizacion") or ""
                return (
                    "<autorizacion><estado>AUTORIZADO</estado>"
                    f"<fechaAutorizacion>{fecha}</fechaAutorizacion>"
                    f"<comprobante><![CDATA[{comprobante}]]></comprobante></autorizacion>"
                )
    return None
