"""Lectura y formato de montos en USD.

Acepta los formatos que aparecen en recibos y estados de cuenta ecuatorianos:
"12.50", "12,50", "1.234,56", "1,234.56", "$ 1 234,56".
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENTAVO = Decimal("0.01")


class MontoInvalido(ValueError):
    pass


def redondear(valor: Decimal) -> Decimal:
    return valor.quantize(CENTAVO, rounding=ROUND_HALF_UP)


def _separador_decimal(s: str) -> str | None:
    if "," in s and "." in s:
        return "," if s.rfind(",") > s.rfind(".") else "."  # el que aparece al final
    for sep in (",", "."):
        if sep in s:
            partes = s.split(sep)
            if len(partes) != 2:
                return None  # "1.234.567": separador de miles
            # En USD no hay 3 decimales: "1.234" son miles, salvo "0.005"
            if len(partes[1]) != 3 or partes[0] in ("", "0"):
                return sep
            return None
    return None


def _normalizar(s: str) -> str | None:
    """'1.234,56' -> '1234.56'. None si el texto no es un número bien formado."""
    decimal_sep = _separador_decimal(s)
    if decimal_sep:
        entero, _, fraccion = s.rpartition(decimal_sep)
        if decimal_sep in entero:
            return None
    else:
        entero, fraccion = s, ""
    for sep in {",", "."} - {decimal_sep}:
        if sep in entero:
            grupos = entero.split(sep)
            if not grupos[0] or len(grupos[0]) > 3 or any(len(g) != 3 for g in grupos[1:]):
                return None
            entero = "".join(grupos)
    entero = entero or "0"
    if not entero.isdigit() or (fraccion and not fraccion.isdigit()) or (decimal_sep and not fraccion):
        return None
    return f"{entero}.{fraccion}" if fraccion else entero


def parse_monto(texto: object) -> Decimal:
    """Convierte texto a Decimal con 2 decimales. Lanza MontoInvalido si no se puede."""
    if isinstance(texto, Decimal):
        return redondear(texto)
    if isinstance(texto, int):
        return redondear(Decimal(texto))
    s = str(texto or "").strip()
    for basura in ("USD", "usd", "US$", "$", " ", " "):
        s = s.replace(basura, "")
    if not s:
        raise MontoInvalido("Escribe un monto.")

    negativo = False
    if s.startswith("(") and s.endswith(")"):
        negativo, s = True, s[1:-1]
    if s.startswith("-"):
        negativo, s = True, s[1:]
    elif s.endswith("-"):  # algunos bancos ponen el signo al final
        negativo, s = True, s[:-1]

    s = _normalizar(s)
    if s is None:
        raise MontoInvalido(f"No entiendo el monto «{texto}».")
    try:
        valor = Decimal(s)
    except InvalidOperation as exc:
        raise MontoInvalido(f"No entiendo el monto «{texto}».") from exc
    if not valor.is_finite():
        raise MontoInvalido(f"No entiendo el monto «{texto}».")
    return redondear(-valor if negativo else valor)


def formato_usd(valor: Decimal | int | float | None, signo: bool = False) -> str:
    """Decimal('1234.5') -> '$1,234.50'. Con signo=True los positivos llevan '+'."""
    if valor is None:
        return "—"
    v = redondear(Decimal(str(valor)))
    texto = f"${abs(v):,.2f}"
    if v < 0:
        return f"−{texto}"
    if signo and v > 0:
        return f"+{texto}"
    return texto


def formato_porcentaje(valor: Decimal | None, decimales: int = 0) -> str:
    """Decimal('0.1834') -> '18%'."""
    if valor is None:
        return "—"
    pct = Decimal(str(valor)) * 100
    texto = f"{abs(pct):.{decimales}f}%"
    return f"−{texto}" if pct < 0 else texto
