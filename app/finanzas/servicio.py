"""Consultas a la base que alimentan los cálculos y los formularios."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import TIPOS_AHORRO, Categoria, Config, Cuenta, Movimiento
from .calculos import CuentaSaldo, Mov

# (nombre, tipo, ícono, rubro SRI sugerido). Los rubros son una sugerencia: revísalos con las reglas del SRI.
CATEGORIAS_BASE: list[tuple[str, str, str, str | None]] = [
    ("Supermercado", "gasto", "🛒", "alimentacion"),
    ("Comida fuera", "gasto", "🍽️", "alimentacion"),
    ("Café y snacks", "gasto", "☕", None),
    ("Vivienda", "gasto", "🏠", "vivienda"),
    ("Servicios básicos", "gasto", "💡", "vivienda"),
    ("Internet y celular", "gasto", "📶", None),
    ("Transporte", "gasto", "🚕", None),
    ("Salud", "gasto", "💊", "salud"),
    ("Educación", "gasto", "📚", "educacion"),
    ("Vestimenta", "gasto", "👕", "vestimenta"),
    ("Gimnasio y deporte", "gasto", "🏋️", None),
    ("Suscripciones", "gasto", "📺", None),
    ("Entretenimiento", "gasto", "🎬", None),
    ("Viajes", "gasto", "✈️", "turismo"),
    ("Cuidado personal", "gasto", "🧴", None),
    ("Regalos", "gasto", "🎁", None),
    ("Comisiones e intereses", "gasto", "🏦", None),
    ("Impuestos", "gasto", "🧾", None),
    ("Otros gastos", "gasto", "📦", None),
    ("Sueldo", "ingreso", "💼", None),
    ("Décimo tercero", "ingreso", "🎄", None),
    ("Décimo cuarto", "ingreso", "📅", None),
    ("Fondos de reserva", "ingreso", "🛟", None),
    ("Utilidades", "ingreso", "📈", None),
    ("Trabajos extra", "ingreso", "🧑‍💻", None),
    ("Intereses ganados", "ingreso", "💰", None),
    ("Reembolsos", "ingreso", "↩️", None),
    ("Otros ingresos", "ingreso", "➕", None),
]


def obtener_config(bd: Session, clave: str) -> str | None:
    fila = bd.get(Config, clave)
    return fila.valor if fila else None


def guardar_config(bd: Session, clave: str, valor: str) -> None:
    fila = bd.get(Config, clave)
    if fila:
        fila.valor = valor
    else:
        bd.add(Config(clave=clave, valor=valor))


def asegurar_categorias_base(bd: Session) -> None:
    """Crea las categorías iniciales una sola vez (si luego las borras, no vuelven)."""
    if obtener_config(bd, "categorias_base_creadas"):
        return
    if bd.scalar(select(func.count()).select_from(Categoria)) == 0:
        for orden, (nombre, tipo, icono, rubro) in enumerate(CATEGORIAS_BASE):
            bd.add(Categoria(nombre=nombre, tipo=tipo, icono=icono, rubro_sri=rubro, orden=orden))
    guardar_config(bd, "categorias_base_creadas", "1")
    bd.commit()


def movs_para_calculo(bd: Session, hasta: date | None = None) -> list[Mov]:
    consulta = select(
        Movimiento.fecha,
        Movimiento.tipo,
        Movimiento.monto,
        Movimiento.cuenta_id,
        Movimiento.cuenta_destino_id,
        Movimiento.categoria_id,
    )
    if hasta is not None:
        consulta = consulta.where(Movimiento.fecha <= hasta)
    return [Mov(*fila) for fila in bd.execute(consulta).all()]


def cuentas_para_calculo(cuentas: list[Cuenta]) -> list[CuentaSaldo]:
    return [CuentaSaldo(c.id, c.tipo, c.saldo_inicial, c.fecha_saldo_inicial) for c in cuentas]


def listar_cuentas(bd: Session, solo_activas: bool = False) -> list[Cuenta]:
    consulta = select(Cuenta).order_by(Cuenta.activa.desc(), Cuenta.orden, Cuenta.nombre)
    if solo_activas:
        consulta = consulta.where(Cuenta.activa.is_(True))
    return list(bd.scalars(consulta))


def listar_categorias(bd: Session, tipo: str | None = None, solo_activas: bool = False) -> list[Categoria]:
    consulta = select(Categoria).order_by(Categoria.tipo, Categoria.orden, Categoria.nombre)
    if tipo:
        consulta = consulta.where(Categoria.tipo == tipo)
    if solo_activas:
        consulta = consulta.where(Categoria.activa.is_(True))
    return list(bd.scalars(consulta))


def categorias_frecuentes(bd: Session, tipo: str, hoy: date, limite: int = 6) -> list[Categoria]:
    """Las categorías más usadas en los últimos 90 días; si no hay historial, las primeras."""
    uso = (
        select(Movimiento.categoria_id, func.count().label("veces"))
        .where(Movimiento.tipo == tipo, Movimiento.fecha >= hoy - timedelta(days=90))
        .group_by(Movimiento.categoria_id)
        .subquery()
    )
    consulta = (
        select(Categoria)
        .outerjoin(uso, uso.c.categoria_id == Categoria.id)
        .where(Categoria.tipo == tipo, Categoria.activa.is_(True))
        .order_by(func.coalesce(uso.c.veces, 0).desc(), Categoria.orden, Categoria.nombre)
        .limit(limite)
    )
    return list(bd.scalars(consulta))


def cuenta_por_defecto(bd: Session, tipo: str) -> int | None:
    """La cuenta usada en el último movimiento de ese tipo; si no hay, la primera activa que no sea pasivo."""
    tipo_bd = "transferencia" if tipo == "ahorro" else tipo
    ultima = bd.scalar(
        select(Movimiento.cuenta_id)
        .join(Cuenta, Cuenta.id == Movimiento.cuenta_id)
        .where(Movimiento.tipo == tipo_bd, Cuenta.activa.is_(True))
        .order_by(Movimiento.fecha.desc(), Movimiento.id.desc())
        .limit(1)
    )
    if ultima:
        return ultima
    for cuenta in listar_cuentas(bd, solo_activas=True):
        if not cuenta.es_pasivo:
            return cuenta.id
    return None


def padre_de(bd: Session) -> dict[int, int | None]:
    return {cid: pid for cid, pid in bd.execute(select(Categoria.id, Categoria.padre_id)).all()}


def presupuestos_datos(bd: Session) -> list:
    from ..models import Presupuesto
    from .planificacion import PresupuestoDato

    return [
        PresupuestoDato(p.id, p.categoria_id, p.monto, p.desde_mes, p.hasta_mes)
        for p in bd.scalars(select(Presupuesto))
    ]


def aportes_por_meta(bd: Session) -> dict[int, list[tuple[date, Decimal]]]:
    from .planificacion import aporte_a_meta

    aportes: dict[int, list[tuple[date, Decimal]]] = {}
    filas = bd.execute(
        select(Movimiento.meta_id, Movimiento.fecha, Movimiento.tipo, Movimiento.monto).where(Movimiento.meta_id.is_not(None))
    ).all()
    for meta_id, fecha, tipo, monto in filas:
        aportes.setdefault(meta_id, []).append((fecha, aporte_a_meta(tipo, monto)))
    return aportes


def progreso_metas(bd: Session, hoy: date, solo_activas: bool = True) -> list[tuple]:
    """[(meta, progreso)] en el orden de la lista."""
    from ..models import MetaAhorro
    from .planificacion import progreso_meta

    consulta = select(MetaAhorro).order_by(MetaAhorro.estado, MetaAhorro.orden, MetaAhorro.id)
    if solo_activas:
        consulta = consulta.where(MetaAhorro.estado == "activa")
    aportes = aportes_por_meta(bd)
    return [
        (m, progreso_meta(m.monto_objetivo, m.saldo_inicial, aportes.get(m.id, []), hoy, m.fecha_objetivo))
        for m in bd.scalars(consulta)
    ]


def destino_ahorro_por_defecto(bd: Session) -> int | None:
    for cuenta in listar_cuentas(bd, solo_activas=True):
        if cuenta.tipo in TIPOS_AHORRO:
            return cuenta.id
    return None
