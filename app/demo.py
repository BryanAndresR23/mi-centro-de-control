"""Datos de ejemplo: 12 meses de finanzas realistas de una persona en Quito.

Se usan con scripts/datos_demo.py (base local) y en las pruebas. Nunca en producción.
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .finanzas.calculos import CuentaSaldo, Mov, saldo_al
from .finanzas.servicio import asegurar_categorias_base
from .models import Categoria, Config, Cuenta, Movimiento
from .tiempo import primer_dia, sumar_meses, ultimo_dia


def _d(valor: float) -> Decimal:
    return Decimal(f"{valor:.2f}")


def cargar_demo(bd: Session, hoy_: date, semilla: int = 2026) -> dict[str, int]:
    """Borra cuentas, categorías y movimientos, y carga los datos de ejemplo."""
    rnd = random.Random(semilla)
    bd.execute(delete(Movimiento))
    bd.execute(delete(Cuenta))
    bd.execute(delete(Categoria))
    bd.execute(delete(Config).where(Config.clave == "categorias_base_creadas"))
    bd.commit()
    asegurar_categorias_base(bd)
    cat = {c.nombre: c.id for c in bd.scalars(select(Categoria))}

    anio0, mes0 = sumar_meses(hoy_.year, hoy_.month, -11)
    inicio = primer_dia(anio0, mes0)

    def cuenta(nombre, tipo, institucion, saldo, orden, **extra) -> Cuenta:
        c = Cuenta(
            nombre=nombre, tipo=tipo, institucion=institucion, saldo_inicial=Decimal(saldo),
            fecha_saldo_inicial=inicio, orden=orden, activa=True, **extra,
        )
        bd.add(c)
        return c

    principal = cuenta("Pichincha ahorros", "ahorros", "Banco Pichincha", "850.00", 1)
    metas = cuenta("Ahorro metas", "ahorros", "Produbanco", "1500.00", 2)
    efectivo = cuenta("Efectivo", "efectivo", None, "60.00", 3)
    visa = cuenta(
        "Visa Pichincha", "tarjeta_credito", "Banco Pichincha", "320.00", 4,
        cupo=Decimal("2500.00"), dia_corte=20, dia_pago=5,
    )
    poliza = cuenta("Póliza a plazo", "inversion", "Cooperativa", "2000.00", 5)
    bd.flush()

    movs: list[Movimiento] = []

    def agregar(fecha: date, tipo: str, monto, cuenta_id: int, categoria: str | None = None,
                comercio: str | None = None, deducible: bool = False, *, destino: int | None = None) -> None:
        if fecha > hoy_ or fecha < inicio:
            return
        movs.append(Movimiento(
            fecha=fecha, tipo=tipo, monto=monto if isinstance(monto, Decimal) else _d(monto),
            cuenta_id=cuenta_id, cuenta_destino_id=destino,
            categoria_id=cat[categoria] if categoria else None,
            comercio=comercio, deducible=deducible, origen="manual",
        ))

    def dia(anio: int, mes: int, d: int) -> date:
        return date(anio, mes, min(d, ultimo_dia(anio, mes).day))

    def dia_al_azar(anio: int, mes: int) -> date:
        return dia(anio, mes, rnd.randint(1, ultimo_dia(anio, mes).day))

    meses = [sumar_meses(anio0, mes0, i) for i in range(12)]
    mes_viaje = meses[7]

    for anio, mes in meses:
        # Ingresos
        agregar(dia(anio, mes, 28), "ingreso", 1480, principal.id, "Sueldo", "Rol de pagos")
        agregar(dia(anio, mes, 30), "ingreso", 13.33, poliza.id, "Intereses ganados", "Interés póliza")
        if mes == 12:
            agregar(dia(anio, mes, 20), "ingreso", 1420, principal.id, "Décimo tercero", "Décimo tercero")
        if mes == 8:
            agregar(dia(anio, mes, 15), "ingreso", 470, principal.id, "Décimo cuarto", "Décimo cuarto")
        if rnd.random() < 0.25:
            agregar(dia_al_azar(anio, mes), "ingreso", rnd.choice([80, 120, 150]), principal.id,
                    "Trabajos extra", "Proyecto freelance")

        # Gastos fijos
        agregar(dia(anio, mes, 5), "gasto", 380, principal.id, "Vivienda", "Arriendo departamento", True)
        agregar(dia(anio, mes, 3), "gasto", 35, principal.id, "Gimnasio y deporte", "Gimnasio")
        agregar(dia(anio, mes, 12), "gasto", rnd.uniform(18, 32), principal.id, "Servicios básicos",
                "Empresa Eléctrica Quito", True)
        agregar(dia(anio, mes, 14), "gasto", rnd.uniform(9, 15), principal.id, "Servicios básicos", "EPMAPS agua", True)
        agregar(dia(anio, mes, 8), "gasto", 27.50, visa.id, "Internet y celular", "Netlife")
        agregar(dia(anio, mes, 10), "gasto", 19.99, visa.id, "Internet y celular", "Claro plan")
        agregar(dia(anio, mes, 16), "gasto", 9.99, visa.id, "Suscripciones", "Netflix")
        agregar(dia(anio, mes, 18), "gasto", 5.99, visa.id, "Suscripciones", "Spotify")
        agregar(dia(anio, mes, 6), "gasto", 60, visa.id, "Educación", "Curso de inglés", True)

        # Supermercado cada sábado
        d = primer_dia(anio, mes)
        while d.month == mes:
            if d.weekday() == 5:
                tienda = rnd.choice(["Supermaxi", "Mi Comisariato", "Tía", "Megamaxi"])
                cuenta_pago = visa.id if rnd.random() < 0.7 else principal.id
                agregar(d, "gasto", rnd.uniform(32, 85), cuenta_pago, "Supermercado", tienda, True)
            d += timedelta(days=1)

        # Variables
        for _ in range(rnd.randint(6, 10)):
            lugar = rnd.choice(["Menestras del Negro", "Almuerzo ejecutivo", "KFC", "Pizzería del barrio", "Cevichería"])
            agregar(dia_al_azar(anio, mes), "gasto", rnd.uniform(4.5, 18), rnd.choice([efectivo.id, visa.id]),
                    "Comida fuera", lugar, rnd.random() < 0.5)
        for _ in range(rnd.randint(10, 16)):
            lugar = rnd.choice(["Sweet & Coffee", "Juan Valdez", "Tienda del barrio", "Panadería"])
            agregar(dia_al_azar(anio, mes), "gasto", rnd.uniform(1.5, 4.5), rnd.choice([efectivo.id, principal.id]),
                    "Café y snacks", lugar)
        for _ in range(rnd.randint(7, 11)):
            agregar(dia_al_azar(anio, mes), "gasto", rnd.uniform(2.5, 9), visa.id, "Transporte",
                    rnd.choice(["Uber", "InDrive", "Cabify"]))
        agregar(dia(anio, mes, 2), "gasto", 10, efectivo.id, "Transporte", "Recarga Metro de Quito")
        agregar(dia(anio, mes, 17), "gasto", 10, efectivo.id, "Transporte", "Recarga Metro de Quito")
        for _ in range(rnd.randint(1, 3)):
            agregar(dia_al_azar(anio, mes), "gasto", rnd.uniform(6, 15), visa.id, "Entretenimiento", "Supercines")
        agregar(dia_al_azar(anio, mes), "gasto", rnd.uniform(8, 12), efectivo.id, "Cuidado personal", "Peluquería")
        if rnd.random() < 0.6:
            agregar(dia_al_azar(anio, mes), "gasto", rnd.uniform(6, 40), visa.id, "Salud",
                    rnd.choice(["Fybeca", "Pharmacys"]), True)
        if rnd.random() < 0.4:
            agregar(dia_al_azar(anio, mes), "gasto", rnd.uniform(25, 90), visa.id, "Vestimenta",
                    rnd.choice(["De Prati", "RM", "Marathon Sports"]), True)
        if mes == 12:
            agregar(dia(anio, mes, 22), "gasto", rnd.uniform(60, 120), visa.id, "Regalos", "Regalos de Navidad")
        if (anio, mes) == mes_viaje:
            agregar(dia(anio, mes, 10), "gasto", 12, efectivo.id, "Viajes", "Bus Quito–Baños", True)
            agregar(dia(anio, mes, 11), "gasto", 120, visa.id, "Viajes", "Hostal en Baños", True)

        # Transferencias: retiros, ahorro mensual
        agregar(dia(anio, mes, 1), "transferencia", 60, principal.id, comercio="Retiro cajero", destino=efectivo.id)
        agregar(dia(anio, mes, 15), "transferencia", 50, principal.id, comercio="Retiro cajero", destino=efectivo.id)
        agregar(dia(anio, mes, 29), "transferencia", 200, principal.id, destino=metas.id)

    # Pago de la tarjeta: el día 5, lo que se debía al corte (día 20) del mes anterior
    cuenta_visa = CuentaSaldo(visa.id, visa.tipo, visa.saldo_inicial, visa.fecha_saldo_inicial)
    for anio, mes in meses:
        fecha_pago = dia(anio, mes, 5)
        if fecha_pago > hoy_:
            break
        anio_c, mes_c = sumar_meses(anio, mes, -1)
        corte = dia(anio_c, mes_c, 20)
        calculo = [Mov(m.fecha, m.tipo, m.monto, m.cuenta_id, m.cuenta_destino_id) for m in movs]
        deuda = -saldo_al(cuenta_visa, calculo, max(corte, inicio))
        if deuda > 0:
            agregar(fecha_pago, "transferencia", deuda, principal.id, destino=visa.id)

    bd.add_all(movs)
    bd.commit()
    return {"cuentas": 5, "categorias": len(cat), "movimientos": len(movs)}
