"""Lectura y validación de los campos que llegan de los formularios."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from ..finanzas.montos import MontoInvalido, parse_monto


class Errores(dict):
    """campo -> mensaje. Vacío = todo bien."""


def texto(valores: dict, campo: str, maximo: int, errores: Errores, obligatorio: bool = False) -> str | None:
    valor = str(valores.get(campo) or "").strip()
    if not valor:
        if obligatorio:
            errores[campo] = "Este campo es obligatorio."
        return None
    if len(valor) > maximo:
        errores[campo] = f"Máximo {maximo} caracteres."
    return valor


def monto(
    valores: dict,
    campo: str,
    errores: Errores,
    obligatorio: bool = True,
    positivo: bool = True,
    permitir_negativo: bool = False,
) -> Decimal | None:
    crudo = str(valores.get(campo) or "").strip()
    if not crudo:
        if obligatorio:
            errores[campo] = "Escribe un monto."
        return None
    try:
        valor = parse_monto(crudo)
    except MontoInvalido as exc:
        errores[campo] = str(exc)
        return None
    if positivo and valor <= 0:
        errores[campo] = "El monto debe ser mayor que cero."
    elif not permitir_negativo and valor < 0:
        errores[campo] = "No puede ser negativo."
    elif abs(valor) >= Decimal("10000000"):
        errores[campo] = "El monto es demasiado grande."
    return valor


def fecha(valores: dict, campo: str, errores: Errores, obligatorio: bool = True) -> date | None:
    crudo = str(valores.get(campo) or "").strip()
    if not crudo:
        if obligatorio:
            errores[campo] = "Elige una fecha."
        return None
    try:
        valor = date.fromisoformat(crudo)
    except ValueError:
        errores[campo] = "Fecha inválida."
        return None
    if not (2000 <= valor.year <= 2100):
        errores[campo] = "Fecha fuera de rango."
    return valor


def entero(valores: dict, campo: str, errores: Errores, minimo: int, maximo: int) -> int | None:
    crudo = str(valores.get(campo) or "").strip()
    if not crudo:
        return None
    try:
        valor = int(crudo)
    except ValueError:
        errores[campo] = "Debe ser un número entero."
        return None
    if not minimo <= valor <= maximo:
        errores[campo] = f"Debe estar entre {minimo} y {maximo}."
    return valor


def solo_digitos(valores: dict, campo: str, longitud: int, errores: Errores) -> str | None:
    valor = str(valores.get(campo) or "").replace(" ", "").strip()
    if not valor:
        return None
    if not (valor.isdigit() and len(valor) == longitud):
        errores[campo] = f"Debe tener {longitud} dígitos."
    return valor


def marcado(valores: dict, campo: str) -> bool:
    return str(valores.get(campo) or "") in ("1", "on", "true", "si")
