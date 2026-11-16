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
from .models import (
    Area,
    Beca,
    BecaRequisito,
    Categoria,
    Config,
    Cuenta,
    Entrenamiento,
    MetaAhorro,
    MetaPersonal,
    Movimiento,
    Postulacion,
    PostulacionEvento,
    Presupuesto,
    Recurrente,
    Rutina,
    Serie,
    SesionEstudio,
    Tarea,
)
from .tiempo import ahora_utc, primer_dia, sumar_meses, ultimo_dia


def _d(valor: float) -> Decimal:
    return Decimal(f"{valor:.2f}")


def cargar_demo(bd: Session, hoy_: date, semilla: int = 2026) -> dict[str, int]:
    """Borra cuentas, categorías y movimientos, y carga los datos de ejemplo."""
    rnd = random.Random(semilla)
    from .models import AvisoEnviado, Extraccion, Informe, ReglaCategoria

    for modelo in (Extraccion, Informe, AvisoEnviado, ReglaCategoria):
        bd.execute(delete(modelo))
    bd.execute(delete(Movimiento))
    bd.execute(delete(Presupuesto))
    bd.execute(delete(Recurrente))
    bd.execute(delete(MetaAhorro))
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

    # Fase 1b: metas (los ahorros mensuales se reparten entre dos metas), presupuestos y pagos fijos
    emergencia = MetaAhorro(nombre="Fondo de emergencia", icono="🛟", monto_objetivo=Decimal("4000"), saldo_inicial=Decimal("1000"), orden=1)
    maestria = MetaAhorro(nombre="Maestría", icono="🎓", monto_objetivo=Decimal("9000"), saldo_inicial=Decimal("500"),
                          fecha_objetivo=date(hoy_.year + 1, 9, 1), orden=2)
    bd.add_all([emergencia, maestria])
    bd.flush()
    for i, m in enumerate(sorted((m for m in movs if m.tipo == "transferencia" and m.cuenta_destino_id == metas.id),
                                 key=lambda x: x.fecha)):
        m.meta_id = emergencia.id if i % 2 == 0 else maestria.id
    inicio_mes = primer_dia(hoy_.year, hoy_.month)
    for nombre, monto in [("Supermercado", "260"), ("Comida fuera", "90"), ("Café y snacks", "35"), ("Transporte", "75"),
                          ("Entretenimiento", "30"), ("Vestimenta", "60")]:
        bd.add(Presupuesto(categoria_id=cat[nombre], monto=Decimal(monto), desde_mes=inicio))
    bd.add_all([
        Recurrente(nombre="Arriendo", tipo="servicio", monto=Decimal("380"), frecuencia="mensual",
                   proxima_fecha=_proximo(hoy_, 5), dia=5, cuenta_id=principal.id, categoria_id=cat["Vivienda"], dias_aviso=3),
        Recurrente(nombre="Netflix", tipo="suscripcion", monto=Decimal("9.99"), frecuencia="mensual",
                   proxima_fecha=_proximo(hoy_, 16), dia=16, cuenta_id=visa.id, categoria_id=cat["Suscripciones"],
                   meses_sin_uso=2, revision_uso=None),
        Recurrente(nombre="Spotify", tipo="suscripcion", monto=Decimal("5.99"), frecuencia="mensual",
                   proxima_fecha=_proximo(hoy_, 18), dia=18, cuenta_id=visa.id, categoria_id=cat["Suscripciones"]),
        Recurrente(nombre="Gimnasio", tipo="suscripcion", monto=Decimal("35"), frecuencia="mensual",
                   proxima_fecha=_proximo(hoy_, 3), dia=3, cuenta_id=principal.id, categoria_id=cat["Gimnasio y deporte"]),
        Recurrente(nombre="Internet Netlife", tipo="servicio", monto=Decimal("27.50"), frecuencia="mensual",
                   proxima_fecha=_proximo(hoy_, 8), dia=8, cuenta_id=visa.id, categoria_id=cat["Internet y celular"]),
        Recurrente(nombre="Pago Visa", tipo="deuda", monto=Decimal("450"), frecuencia="mensual",
                   proxima_fecha=_proximo(hoy_, 5), dia=5, cuenta_id=principal.id, cuenta_destino_id=visa.id, dias_aviso=5),
    ])
    bd.add_all(movs)
    bd.commit()
    _demo_pendientes(bd, hoy_, rnd)
    return {"cuentas": 5, "categorias": len(cat), "movimientos": len(movs), "inicio_mes": inicio_mes.isoformat()}


def _proximo(hoy_: date, dia: int) -> date:
    """La próxima fecha (hoy o después) con ese día del mes."""
    fecha = date(hoy_.year, hoy_.month, min(dia, ultimo_dia(hoy_.year, hoy_.month).day))
    if fecha < hoy_:
        a, m = sumar_meses(hoy_.year, hoy_.month, 1)
        fecha = date(a, m, min(dia, ultimo_dia(a, m).day))
    return fecha


def _demo_pendientes(bd: Session, hoy_: date, rnd: random.Random) -> None:
    from .rutas.pendientes import REQUISITOS_BECA, asegurar_areas_base

    for modelo in (Serie, Entrenamiento, Rutina, SesionEstudio, PostulacionEvento, Postulacion, BecaRequisito, Beca, Tarea,
                   MetaPersonal, Area):
        bd.execute(delete(modelo))
    bd.execute(delete(Config).where(Config.clave == "areas_base_creadas"))
    bd.commit()
    asegurar_areas_base(bd)
    area = {a.plantilla: a for a in bd.scalars(select(Area))}
    d = lambda n: hoy_ + timedelta(days=n)  # noqa: E731
    meta_beca = MetaPersonal(area_id=area["metas"].id, titulo="Ganar una beca para la maestría en datos", horizonte="anual",
                             periodo=str(hoy_.year))
    meta_ingles = MetaPersonal(area_id=area["metas"].id, titulo="Llegar a C1 en inglés", horizonte="trimestral",
                               periodo=f"{hoy_.year}-T{(hoy_.month - 1) // 3 + 1}")
    bd.add_all([meta_beca, meta_ingles])
    bd.flush()
    tareas = [
        ("general", "Entregar informe mensual de conciliación", d(0), 1, None, None),
        ("general", "Preparar presentación del dashboard de KPIs", d(2), 2, None, None),
        ("general", "Revisar correos pendientes del equipo", d(-1), 2, None, None),
        ("general", "Reunión semanal con el área", d(3), 3, "semanal", None),
        ("habilidad", "Resolver 5 ejercicios de window functions", d(1), 2, None, None),
        ("habilidad", "Proyecto: modelo de datos de finanzas personales en Power BI", d(12), 2, None, None),
        ("idioma", "Practicar speaking 20 min", d(0), 2, "diaria", meta_ingles.id),
        ("idioma", "Simulacro completo de IELTS", d(9), 1, None, meta_ingles.id),
        ("becas", "Pedir carta de recomendación al jefe", d(4), 1, None, meta_beca.id),
        ("becas", "Terminar borrador del ensayo", d(10), 1, None, meta_beca.id),
        ("metas", "Inscribirme al examen IELTS", d(-3), 1, None, meta_beca.id),
        ("postulaciones", "Actualizar CV y LinkedIn", d(5), 2, None, None),
    ]
    for plantilla, titulo, fecha, prioridad, recurrencia, meta in tareas:
        bd.add(Tarea(area_id=area[plantilla].id, titulo=titulo, fecha_limite=fecha, prioridad=prioridad,
                     recurrencia=recurrencia, meta_personal_id=meta))
    hecha = Tarea(area_id=area["metas"].id, titulo="Elegir 3 programas de maestría", fecha_limite=d(-10), prioridad=2,
                  meta_personal_id=meta_beca.id, estado="hecha", completada_en=ahora_utc())
    bd.add(hecha)
    for i in range(1, 22):
        dia = d(-i)
        if rnd.random() < 0.75 or i <= 4:
            bd.add(SesionEstudio(area_id=area["idioma"].id, fecha=dia, minutos=rnd.choice([20, 30, 45, 60]),
                                 tema=rnd.choice(["Listening", "Speaking", "Writing task 2", "Vocabulario"])))
        if rnd.random() < 0.4:
            bd.add(SesionEstudio(area_id=area["habilidad"].id, fecha=dia, minutos=rnd.choice([30, 45, 60, 90]),
                                 tema=rnd.choice(["JOINs", "Window functions", "CTEs", "Índices"])))
    rutinas = [(0, "Pierna", "Sentadilla 4x8\nPrensa 3x12\nZancadas 3x10"), (2, "Empuje", "Press banca 4x8\nPress militar 3x10\nFondos 3x12"),
               (4, "Tirón", "Peso muerto 4x6\nDominadas 4x8\nRemo 3x10")]
    for dia_semana, nombre, ejercicios in rutinas:
        bd.add(Rutina(dia_semana=dia_semana, nombre=nombre, ejercicios=ejercicios))
    for semana in range(6):
        for dia_semana, nombre, _ in rutinas:
            fecha = d(-(semana * 7) - ((hoy_.weekday() - dia_semana) % 7))
            if fecha > hoy_ or rnd.random() < 0.15:
                continue
            e = Entrenamiento(fecha=fecha, rutina=nombre, duracion_min=rnd.choice([50, 60, 70]))
            base = {"Pierna": ("Sentadilla", 80), "Empuje": ("Press banca", 60), "Tirón": ("Peso muerto", 100)}[nombre]
            for _ in range(4):
                e.series.append(Serie(ejercicio=base[0], repeticiones=8, peso=Decimal(base[1] + (6 - semana) * 2.5)))
            bd.add(e)
    postulaciones = [
        ("Fintech Andina", "Data Analyst", "entrevista", "Entrevista técnica", d(2)),
        ("Banco del Pacífico", "Analista de Riesgos", "postulado", "Esperar respuesta", d(10)),
        ("Remote Co.", "BI Developer (remoto)", "oferta", "Responder oferta", d(1)),
        ("Consultora XYZ", "Analista Power BI", "rechazo", None, None),
    ]
    for empresa, cargo, estado, paso, fecha in postulaciones:
        p = Postulacion(empresa=empresa, cargo=cargo, fecha_postulacion=d(-20), estado=estado, proximo_paso=paso,
                        fecha_proximo_paso=fecha, enlace="https://example.com/empleo")
        bd.add(p)
        bd.flush()
        bd.add(PostulacionEvento(postulacion_id=p.id, fecha=d(-20), estado="postulado"))
        if estado != "postulado":
            bd.add(PostulacionEvento(postulacion_id=p.id, fecha=d(-5), estado=estado))
    for programa, universidad, pais, beca, dias, hechos in [
        ("MSc Data Science", "University of Edinburgh", "Reino Unido", "Chevening", 25, 3),
        ("Maestría en Ciencia de Datos", "Universidad San Francisco de Quito", "Ecuador", "Beca de excelencia", 60, 5),
    ]:
        b = Beca(programa=programa, universidad=universidad, pais=pais, beca=beca, fecha_limite=d(dias), estado="preparando")
        for orden, requisito in enumerate(REQUISITOS_BECA):
            b.requisitos.append(BecaRequisito(requisito=requisito, orden=orden, hecho=orden < hechos))
        bd.add(b)
    bd.commit()
