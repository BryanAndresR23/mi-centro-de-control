from datetime import date
from decimal import Decimal as D

from app.finanzas.calculos import (
    CuentaSaldo,
    Mov,
    activos_y_pasivos,
    gasto_por_categoria,
    patrimonio_neto,
    resumen_mes,
    saldo_al,
    serie_mensual,
    serie_patrimonio,
    tasa_ahorro,
    variacion,
)

BANCO = CuentaSaldo(1, "ahorros", D("1000.00"), date(2026, 1, 1))
TARJETA = CuentaSaldo(2, "tarjeta_credito", D("200.00"), date(2026, 1, 1))  # debe 200
AHORRO = CuentaSaldo(3, "ahorros", D("0.00"), date(2026, 1, 1))


def test_ingreso_y_gasto_mueven_el_saldo():
    movs = [
        Mov(date(2026, 1, 5), "ingreso", D("1500.00"), 1),
        Mov(date(2026, 1, 6), "gasto", D("380.00"), 1),
    ]
    assert saldo_al(BANCO, movs, date(2026, 1, 31)) == D("2120.00")
    # Antes del gasto
    assert saldo_al(BANCO, movs, date(2026, 1, 5)) == D("2500.00")


def test_movimientos_futuros_no_cuentan():
    movs = [Mov(date(2026, 3, 1), "gasto", D("50.00"), 1)]
    assert saldo_al(BANCO, movs, date(2026, 2, 28)) == D("1000.00")


def test_tarjeta_es_pasivo_y_su_pago_es_transferencia():
    movs = [
        Mov(date(2026, 1, 10), "gasto", D("100.00"), 2),  # compra con tarjeta: la deuda sube a 300
        Mov(date(2026, 1, 15), "transferencia", D("300.00"), 1, cuenta_destino_id=2),  # pago total
    ]
    corte = date(2026, 1, 31)
    assert saldo_al(TARJETA, movs, date(2026, 1, 10)) == D("-300.00")
    assert saldo_al(TARJETA, movs, corte) == D("0.00")
    assert saldo_al(BANCO, movs, corte) == D("700.00")
    # Pagar la tarjeta no es gasto: el único gasto del mes es la compra
    assert resumen_mes(movs, 2026, 1).gastos == D("100.00")


def test_patrimonio_suma_activos_y_resta_deudas():
    movs = [Mov(date(2026, 1, 10), "gasto", D("50.00"), 2)]
    corte = date(2026, 1, 31)
    activos, pasivos = activos_y_pasivos([BANCO, TARJETA], movs, corte)
    assert activos == D("1000.00")
    assert pasivos == D("250.00")
    assert patrimonio_neto([BANCO, TARJETA], movs, corte) == D("750.00")


def test_ahorro_no_cambia_el_patrimonio():
    movs = [Mov(date(2026, 1, 20), "transferencia", D("200.00"), 1, cuenta_destino_id=3)]
    corte = date(2026, 1, 31)
    assert saldo_al(AHORRO, movs, corte) == D("200.00")
    assert patrimonio_neto([BANCO, AHORRO], movs, corte) == D("1000.00")
    assert resumen_mes(movs, 2026, 1).ingresos == D("0")
    assert resumen_mes(movs, 2026, 1).gastos == D("0")


def test_saldo_antes_de_la_fecha_inicial_se_retrocede():
    # Saldo de 1000 al empezar el 1 de enero; en diciembre hubo un ingreso de 300 y un gasto de 100
    movs = [
        Mov(date(2025, 12, 10), "ingreso", D("300.00"), 1),
        Mov(date(2025, 12, 20), "gasto", D("100.00"), 1),
    ]
    assert saldo_al(BANCO, movs, date(2025, 12, 31)) == D("1000.00")
    assert saldo_al(BANCO, movs, date(2025, 12, 15)) == D("1100.00")
    assert saldo_al(BANCO, movs, date(2025, 12, 1)) == D("800.00")
    # El saldo de hoy no cambia por importar historia anterior
    assert saldo_al(BANCO, movs, date(2026, 1, 31)) == D("1000.00")


def test_resumen_y_tasa_de_ahorro():
    movs = [
        Mov(date(2026, 2, 28), "ingreso", D("1500.00"), 1),
        Mov(date(2026, 2, 5), "gasto", D("1200.00"), 1),
        Mov(date(2026, 3, 1), "gasto", D("999.00"), 1),  # otro mes
    ]
    r = resumen_mes(movs, 2026, 2)
    assert (r.ingresos, r.gastos, r.flujo) == (D("1500.00"), D("1200.00"), D("300.00"))
    assert r.tasa_ahorro == D("0.2000")


def test_tasa_de_ahorro_sin_ingresos_es_none():
    assert tasa_ahorro(D("0"), D("50")) is None


def test_tasa_de_ahorro_negativa_si_gastas_mas_de_lo_que_ganas():
    assert tasa_ahorro(D("1000"), D("1250")) == D("-0.2500")


def test_gasto_por_categoria_agrupa_subcategorias_y_ordena():
    padre_de = {10: None, 11: 10, 20: None}  # 11 es subcategoría de 10
    movs = [
        Mov(date(2026, 4, 1), "gasto", D("30.00"), 1, categoria_id=11),
        Mov(date(2026, 4, 2), "gasto", D("20.00"), 1, categoria_id=10),
        Mov(date(2026, 4, 3), "gasto", D("40.00"), 1, categoria_id=20),
        Mov(date(2026, 4, 4), "ingreso", D("999.00"), 1, categoria_id=20),
        Mov(date(2026, 5, 1), "gasto", D("500.00"), 1, categoria_id=20),
    ]
    resultado = gasto_por_categoria(movs, date(2026, 4, 1), date(2026, 4, 30), padre_de)
    assert resultado == [(10, D("50.00")), (20, D("40.00"))]


def test_categoria_con_ciclo_no_se_cuelga():
    padre_de = {1: 2, 2: 1}
    movs = [Mov(date(2026, 4, 1), "gasto", D("5.00"), 1, categoria_id=1)]
    assert len(gasto_por_categoria(movs, date(2026, 4, 1), date(2026, 4, 30), padre_de)) == 1


def test_serie_mensual_respeta_el_orden_y_rellena_meses_vacios():
    movs = [
        Mov(date(2026, 1, 3), "ingreso", D("100.00"), 1),
        Mov(date(2026, 3, 3), "gasto", D("40.00"), 1),
    ]
    serie = serie_mensual(movs, [(2026, 1), (2026, 2), (2026, 3)])
    assert [(r.ingresos, r.gastos) for r in serie] == [
        (D("100.00"), D("0")),
        (D("0"), D("0")),
        (D("0"), D("40.00")),
    ]


def test_serie_patrimonio_usa_hoy_para_el_mes_en_curso():
    movs = [
        Mov(date(2026, 1, 10), "ingreso", D("100.00"), 1),
        Mov(date(2026, 2, 20), "ingreso", D("50.00"), 1),  # después de "hoy"
    ]
    serie = serie_patrimonio([BANCO], movs, [(2026, 1), (2026, 2)], hoy=date(2026, 2, 15))
    assert serie == [D("1100.00"), D("1100.00")]


def test_variacion():
    assert variacion(D("135"), D("100")) == D("0.3500")
    assert variacion(D("80"), D("100")) == D("-0.2000")
    assert variacion(D("50"), D("0")) is None
