"""Importación de estados de cuenta CSV o Excel: lectura, detección de columnas y normalización."""

from __future__ import annotations

import csv
import hashlib
import io
import re
import unicodedata
from collections import Counter
from datetime import date, datetime
from decimal import Decimal

from ..finanzas.montos import MontoInvalido, parse_monto


class ArchivoInvalido(ValueError):
    pass


CLAVES = {
    "fecha": ("fecha", "date", "f. transaccion", "fecha transaccion", "fecha valor", "fecha operacion"),
    "descripcion": ("descripcion", "concepto", "detalle", "referencia", "transaccion", "movimiento", "glosa", "establecimiento"),
    "monto": ("monto", "valor", "importe", "amount"),
    "debito": ("debito", "cargo", "retiro", "egreso", "debe"),
    "credito": ("credito", "abono", "deposito", "ingreso", "haber"),
}


def _simple(texto) -> str:
    texto = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", texto).strip()


def _separador(texto: str) -> str:
    """El separador que aparece la misma cantidad de veces en más líneas (las comas decimales no engañan)."""
    lineas = [linea for linea in texto.splitlines()[:40] if linea.strip()]
    mejor, puntaje_mejor = ",", 0
    for candidato in (";", "\t", "|", ","):
        conteos = Counter(linea.count(candidato) for linea in lineas)
        conteos.pop(0, None)
        if conteos:
            veces, frecuencia = conteos.most_common(1)[0]
            puntaje = veces * frecuencia
            if puntaje > puntaje_mejor:
                mejor, puntaje_mejor = candidato, puntaje
    return mejor


def leer_tabla(contenido: bytes, nombre: str) -> list[list[str]]:
    """Filas como listas de texto. Acepta .csv (coma, punto y coma o tabulador) y .xlsx."""
    nombre = nombre.lower()
    if nombre.endswith(".xlsx") or contenido[:2] == b"PK":
        from openpyxl import load_workbook

        try:
            libro = load_workbook(io.BytesIO(contenido), read_only=True, data_only=True)
        except Exception as exc:  # noqa: BLE001 - openpyxl lanza varios tipos
            raise ArchivoInvalido("No pude abrir el Excel. Guárdalo como .xlsx o .csv.") from exc
        hoja = libro.worksheets[0]
        filas = []
        for fila in hoja.iter_rows(values_only=True):
            filas.append(["" if v is None else (v.strftime("%Y-%m-%d") if isinstance(v, (date, datetime)) else str(v)) for v in fila])
        return [f for f in filas if any(c.strip() for c in f)]
    if nombre.endswith(".xls"):
        raise ArchivoInvalido("El formato .xls antiguo no se puede leer: ábrelo en Excel y guárdalo como .xlsx o .csv.")
    for codificacion in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            texto = contenido.decode(codificacion)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ArchivoInvalido("No pude leer el texto del archivo.")
    filas = [fila for fila in csv.reader(io.StringIO(texto), delimiter=_separador(texto)) if any(c.strip() for c in fila)]
    if not filas:
        raise ArchivoInvalido("El archivo está vacío.")
    return filas


def detectar_columnas(filas: list[list[str]]) -> tuple[int, dict[str, int | None]]:
    """(índice de la fila de encabezados, {campo: índice de columna}) adivinados por palabras clave."""
    mejor_fila, mejor = 0, {}
    for i, fila in enumerate(filas[:25]):
        encontrados: dict[str, int] = {}
        for j, celda in enumerate(fila):
            texto = _simple(celda)
            for campo, claves in CLAVES.items():
                if campo not in encontrados and any(texto == c or texto.startswith(c) for c in claves):
                    encontrados[campo] = j
                    break
        if len(encontrados) > len(mejor):
            mejor_fila, mejor = i, encontrados
        if len(mejor) >= 3:
            break
    columnas = {campo: mejor.get(campo) for campo in CLAVES}
    return mejor_fila, columnas


def parse_fecha(texto: str) -> date | None:
    texto = str(texto or "").strip()[:19]
    for formato in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y", "%Y/%m/%d", "%d.%m.%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            continue
    return None


def _monto_o_none(texto: str) -> Decimal | None:
    if not str(texto or "").strip():
        return None
    try:
        return parse_monto(texto)
    except MontoInvalido:
        return None


def normalizar_filas(
    filas: list[list[str]], encabezado: int, columnas: dict[str, int | None], positivos_son_gastos: bool = False
) -> tuple[list[dict], int]:
    """→ ([{fecha, descripcion, monto (con signo: negativo = gasto)}], filas descartadas)."""
    resultado, descartadas = [], 0

    def celda(fila, campo):
        indice = columnas.get(campo)
        return fila[indice] if indice is not None and indice < len(fila) else ""

    for fila in filas[encabezado + 1 :]:
        fecha = parse_fecha(celda(fila, "fecha"))
        if columnas.get("monto") is not None:
            monto = _monto_o_none(celda(fila, "monto"))
            if monto is not None and positivos_son_gastos:
                monto = -monto
        else:
            debito, credito = _monto_o_none(celda(fila, "debito")), _monto_o_none(celda(fila, "credito"))
            monto = None
            if debito:
                monto = -abs(debito)
            elif credito:
                monto = abs(credito)
        if fecha is None or not monto:
            descartadas += 1  # totales, saldos o filas vacías
            continue
        descripcion = re.sub(r"\s+", " ", celda(fila, "descripcion")).strip()[:120] or "Sin descripción"
        resultado.append({"fecha": fecha.isoformat(), "descripcion": descripcion, "monto": str(monto)})
    return resultado, descartadas


def hashes(filas: list[dict], cuenta_id: int) -> list[str]:
    """Huella de cada fila para no importarla dos veces. Filas idénticas en el mismo archivo se numeran."""
    vistos: dict[str, int] = {}
    salida = []
    for f in filas:
        base = f"{cuenta_id}|{f['fecha']}|{f['monto']}|{_simple(f['descripcion'])}"
        vistos[base] = vistos.get(base, 0) + 1
        salida.append(hashlib.sha256(f"{base}|{vistos[base]}".encode()).hexdigest())
    return salida
