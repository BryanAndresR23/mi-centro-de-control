"""Tablas de la base de datos. Nombres en español para consultarlas con SQL o Power BI."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .tiempo import ahora_utc

# Montos siempre exactos: NUMERIC(12,2) en la base y Decimal en Python, nunca float
Dinero = Numeric(12, 2)

TIPOS_CUENTA = {
    "efectivo": "Efectivo",
    "ahorros": "Cuenta de ahorros",
    "corriente": "Cuenta corriente",
    "tarjeta_credito": "Tarjeta de crédito",
    "inversion": "Inversión",
    "prestamo": "Préstamo",
}
# En pasivos, el saldo inicial es lo que debes (positivo)
TIPOS_PASIVO = frozenset({"tarjeta_credito", "prestamo"})
# Destinos válidos para un "ahorro"
TIPOS_AHORRO = frozenset({"ahorros", "inversion"})

TIPOS_MOVIMIENTO = {"gasto": "Gasto", "ingreso": "Ingreso", "transferencia": "Transferencia"}

ORIGENES = {
    "manual": "Manual",
    "foto": "Foto",
    "xml": "XML del SRI",
    "importacion": "Importación",
    "recurrente": "Recurrente",
    "restauracion": "Restauración",
}

# Rubros de gastos personales deducibles del SRI
RUBROS_SRI = {
    "vivienda": "Vivienda",
    "educacion": "Educación, arte y cultura",
    "salud": "Salud",
    "vestimenta": "Vestimenta",
    "alimentacion": "Alimentación",
    "turismo": "Turismo",
}


class Cuenta(Base):
    __tablename__ = "cuentas"
    __table_args__ = (
        CheckConstraint(
            "tipo IN ('efectivo','ahorros','corriente','tarjeta_credito','inversion','prestamo')",
            name="tipo_valido",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nombre: Mapped[str] = mapped_column(String(80))
    tipo: Mapped[str] = mapped_column(String(20))
    institucion: Mapped[str | None] = mapped_column(String(80))
    saldo_inicial: Mapped[Decimal] = mapped_column(Dinero, default=Decimal("0.00"))
    fecha_saldo_inicial: Mapped[date] = mapped_column(Date)
    cupo: Mapped[Decimal | None] = mapped_column(Dinero)
    dia_corte: Mapped[int | None] = mapped_column(Integer)
    dia_pago: Mapped[int | None] = mapped_column(Integer)
    activa: Mapped[bool] = mapped_column(Boolean, default=True)
    orden: Mapped[int] = mapped_column(Integer, default=0)
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=ahora_utc)

    @property
    def es_pasivo(self) -> bool:
        return self.tipo in TIPOS_PASIVO

    @property
    def tipo_nombre(self) -> str:
        return TIPOS_CUENTA.get(self.tipo, self.tipo)


class Categoria(Base):
    __tablename__ = "categorias"
    __table_args__ = (CheckConstraint("tipo IN ('ingreso','gasto')", name="tipo_valido"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nombre: Mapped[str] = mapped_column(String(60))
    tipo: Mapped[str] = mapped_column(String(10))
    icono: Mapped[str | None] = mapped_column(String(16))
    rubro_sri: Mapped[str | None] = mapped_column(String(20))
    padre_id: Mapped[int | None] = mapped_column(ForeignKey("categorias.id"))
    activa: Mapped[bool] = mapped_column(Boolean, default=True)
    orden: Mapped[int] = mapped_column(Integer, default=0)

    padre: Mapped[Categoria | None] = relationship(remote_side=[id])

    @property
    def etiqueta(self) -> str:
        return f"{self.icono} {self.nombre}" if self.icono else self.nombre


class Movimiento(Base):
    __tablename__ = "movimientos"
    __table_args__ = (
        CheckConstraint("monto > 0", name="monto_positivo"),
        CheckConstraint("tipo IN ('ingreso','gasto','transferencia')", name="tipo_valido"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fecha: Mapped[date] = mapped_column(Date, index=True)
    tipo: Mapped[str] = mapped_column(String(15))
    monto: Mapped[Decimal] = mapped_column(Dinero)
    cuenta_id: Mapped[int] = mapped_column(ForeignKey("cuentas.id"), index=True)
    cuenta_destino_id: Mapped[int | None] = mapped_column(ForeignKey("cuentas.id"))
    categoria_id: Mapped[int | None] = mapped_column(ForeignKey("categorias.id"), index=True)
    comercio: Mapped[str | None] = mapped_column(String(120))
    nota: Mapped[str | None] = mapped_column(Text)
    # Datos de factura (se llenan a mano o, desde la fase 2, desde la foto o el XML del SRI)
    subtotal: Mapped[Decimal | None] = mapped_column(Dinero)
    iva: Mapped[Decimal | None] = mapped_column(Dinero)
    ruc_emisor: Mapped[str | None] = mapped_column(String(13))
    clave_acceso: Mapped[str | None] = mapped_column(String(49), unique=True)
    deducible: Mapped[bool] = mapped_column(Boolean, default=False)
    origen: Mapped[str] = mapped_column(String(15), default="manual")
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=ahora_utc)
    actualizado_en: Mapped[datetime] = mapped_column(DateTime, default=ahora_utc, onupdate=ahora_utc)

    cuenta: Mapped[Cuenta] = relationship(foreign_keys=[cuenta_id])
    cuenta_destino: Mapped[Cuenta | None] = relationship(foreign_keys=[cuenta_destino_id])
    categoria: Mapped[Categoria | None] = relationship()


class Config(Base):
    """Valores sueltos de la app (p. ej. último código 2FA usado)."""

    __tablename__ = "config"

    clave: Mapped[str] = mapped_column(String(50), primary_key=True)
    valor: Mapped[str] = mapped_column(Text)


class IntentoLogin(Base):
    __tablename__ = "intentos_login"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ip: Mapped[str] = mapped_column(String(64), index=True)
    momento: Mapped[datetime] = mapped_column(DateTime, default=ahora_utc, index=True)
    exito: Mapped[bool] = mapped_column(Boolean, default=False)
