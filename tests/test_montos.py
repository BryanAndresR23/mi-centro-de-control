from decimal import Decimal

import pytest

from app.finanzas.montos import MontoInvalido, formato_porcentaje, formato_usd, parse_monto


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("12.50", "12.50"),
        ("12,50", "12.50"),
        ("12,5", "12.50"),
        ("1.234,56", "1234.56"),
        ("1,234.56", "1234.56"),
        ("1,234", "1234.00"),  # coma de miles
        ("1.234", "1234.00"),  # en USD no hay tres decimales: es punto de miles
        ("1.234.567", "1234567.00"),
        ("$ 1 234,56", "1234.56"),
        ("USD 45", "45.00"),
        ("-15.30", "-15.30"),
        ("15.30-", "-15.30"),
        ("(15.30)", "-15.30"),
        ("0.005", "0.01"),  # redondeo comercial (half up)
        ("7", "7.00"),
        (".5", "0.50"),
    ],
)
def test_parse_monto_formatos(texto, esperado):
    assert parse_monto(texto) == Decimal(esperado)


@pytest.mark.parametrize("texto", ["", "   ", "abc", "12..5", "1,2,3,4x", "NaN", "Infinity", "--5"])
def test_parse_monto_invalido(texto):
    with pytest.raises(MontoInvalido):
        parse_monto(texto)


def test_parse_monto_devuelve_decimal_exacto():
    total = sum((parse_monto("0.10") for _ in range(10)), Decimal("0"))
    assert total == Decimal("1.00")


def test_formato_usd():
    assert formato_usd(Decimal("1234.5")) == "$1,234.50"
    assert formato_usd(Decimal("-20")) == "−$20.00"
    assert formato_usd(Decimal("20"), signo=True) == "+$20.00"
    assert formato_usd(Decimal("0"), signo=True) == "$0.00"
    assert formato_usd(None) == "—"


def test_formato_porcentaje():
    assert formato_porcentaje(Decimal("0.1834")) == "18%"
    assert formato_porcentaje(Decimal("-0.05")) == "−5%"
    assert formato_porcentaje(Decimal("0.1834"), 1) == "18.3%"
    assert formato_porcentaje(None) == "—"
