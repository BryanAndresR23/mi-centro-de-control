"""Lectura de recibos: foto (Claude con visión) o XML del SRI, y normalización de los datos."""

from __future__ import annotations

import base64
import re
from datetime import date
from decimal import Decimal

from ..finanzas.montos import MontoInvalido, parse_monto
from ..ia import pedir_json
from . import sri

TIPOS_DOCUMENTO = ["factura", "nota_de_venta", "comprobante_transferencia", "recibo", "ticket", "otro"]
TIPOS_IMAGEN = {"image/jpeg", "image/png", "image/webp", "image/gif"}

SISTEMA = """Eres un asistente que lee comprobantes de compra de Ecuador (moneda: USD).
Extrae los datos tal como aparecen en la imagen; si un dato no se ve, devuelve null. No inventes valores.
Reglas:
- RUC: 13 dígitos. Clave de acceso / número de autorización de factura electrónica: 49 dígitos.
- Fechas en formato AAAA-MM-DD (en Ecuador se escriben DD/MM/AAAA).
- Montos como texto con punto decimal y 2 decimales (ej. "12.50"). total = importe final pagado.
- IVA: el valor del impuesto (no el porcentaje). Tarifas comunes: 15 %, 5 % y 0 %.
- Si la factura es a "CONSUMIDOR FINAL" o la identificación es 9999999999999, consumidor_final = true.
- En una captura de transferencia bancaria, comercio = beneficiario y tipo_documento = comprobante_transferencia.
- categoria_sugerida: elige UNA de la lista dada que mejor describa la compra."""


def esquema_recibo(categorias: list[str]) -> dict:
    texto_o_nulo = {"anyOf": [{"type": "string"}, {"type": "null"}]}
    return {
        "type": "object",
        "properties": {
            "comercio": texto_o_nulo,
            "ruc": texto_o_nulo,
            "fecha": texto_o_nulo,
            "subtotal": texto_o_nulo,
            "iva": texto_o_nulo,
            "total": texto_o_nulo,
            "clave_acceso": texto_o_nulo,
            "tipo_documento": {"type": "string", "enum": TIPOS_DOCUMENTO},
            "consumidor_final": {"anyOf": [{"type": "boolean"}, {"type": "null"}]},
            "categoria_sugerida": {"type": "string", "enum": categorias},
            "confianza": {"type": "number"},
            "observaciones": texto_o_nulo,
        },
        "required": ["comercio", "ruc", "fecha", "subtotal", "iva", "total", "clave_acceso", "tipo_documento",
                     "consumidor_final", "categoria_sugerida", "confianza", "observaciones"],
        "additionalProperties": False,
    }


def extraer_de_imagen(contenido: bytes, tipo_mime: str, categorias: list[str]) -> tuple[dict, str]:
    """Pide a Claude los datos del comprobante. Devuelve (datos crudos, modelo usado)."""
    imagen = {
        "type": "image",
        "source": {"type": "base64", "media_type": tipo_mime, "data": base64.standard_b64encode(contenido).decode()},
    }
    instruccion = {"type": "text", "text": "Lee este comprobante. Categorías posibles: " + ", ".join(categorias)}
    return pedir_json(SISTEMA, [imagen, instruccion], esquema_recibo(categorias), max_tokens=3000)


def _monto(valor) -> str | None:
    if valor in (None, ""):
        return None
    try:
        monto = parse_monto(valor)
    except MontoInvalido:
        return None
    return str(abs(monto))


def normalizar(datos: dict, hoy: date) -> dict:
    """Limpia lo extraído: montos exactos, fecha válida, RUC y clave solo con dígitos, y avisos de coherencia."""
    limpio = dict(datos)
    for campo in ("subtotal", "iva", "total", "propina"):
        limpio[campo] = _monto(datos.get(campo))
    ruc = re.sub(r"\D", "", datos.get("ruc") or "")
    limpio["ruc"] = ruc if len(ruc) == 13 else None
    clave = re.sub(r"\D", "", datos.get("clave_acceso") or "")
    limpio["clave_acceso"] = clave if sri.clave_valida(clave) else None

    fecha = None
    try:
        fecha = date.fromisoformat(str(datos.get("fecha") or "")[:10])
    except ValueError:
        pass
    if fecha is None and limpio["clave_acceso"]:
        fecha_clave = sri.info_clave(limpio["clave_acceso"])["fecha"]
        fecha = date.fromisoformat(fecha_clave) if fecha_clave else None
    if fecha is not None and not (date(2000, 1, 1) <= fecha <= date(hoy.year + 1, 12, 31)):
        fecha = None
    limpio["fecha"] = (fecha or hoy).isoformat()

    avisos = list(datos.get("avisos") or [])
    if fecha is None:
        avisos.append("No se leyó la fecha: puse la de hoy.")
    if limpio["total"] is None:
        avisos.append("No se leyó el total.")
    elif limpio["subtotal"] and limpio["iva"]:
        suma = Decimal(limpio["subtotal"]) + Decimal(limpio["iva"]) + Decimal(limpio.get("propina") or "0")
        if abs(suma - Decimal(limpio["total"])) > Decimal("0.05"):
            avisos.append(f"Subtotal + IVA ({suma}) no coincide con el total ({limpio['total']}).")
    if datos.get("clave_acceso") and not limpio["clave_acceso"]:
        avisos.append("La clave de acceso leída no es válida (dígito verificador).")
    if datos.get("consumidor_final"):
        avisos.append("La factura salió a CONSUMIDOR FINAL: no sirve como gasto personal deducible.")
    limpio["avisos"] = avisos
    comercio = (datos.get("comercio") or "").strip()
    limpio["comercio"] = comercio[:120] or None
    return limpio


def completar_con_sri(datos: dict) -> dict:
    """Si hay clave de acceso válida, intenta traer el XML autorizado del SRI (valores exactos)."""
    clave = datos.get("clave_acceso")
    if not clave or datos.get("fuente_valores") == "sri":
        return datos
    xml = sri.consultar_autorizacion(clave)
    if not xml:
        return {**datos, "avisos": [*datos.get("avisos", []), "No se pudo consultar al SRI; revisa los valores."]}
    try:
        oficial = sri.parsear_xml(xml)
    except sri.XMLInvalido:
        return datos
    combinado = {**datos, **{k: v for k, v in oficial.items() if v not in (None, "", [])}}
    combinado["fuente_valores"] = "sri"
    return combinado
