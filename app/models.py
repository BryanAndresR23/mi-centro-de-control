"""Tablas de la base de datos. Nombres en español para consultarlas con SQL o Power BI."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
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
    # Fase 1b: aporte a una meta y pago de un recurrente. Fase 2: foto e importación.
    meta_id: Mapped[int | None] = mapped_column(ForeignKey("metas_ahorro.id"), index=True)
    recurrente_id: Mapped[int | None] = mapped_column(ForeignKey("recurrentes.id"))
    adjunto_id: Mapped[int | None] = mapped_column(ForeignKey("adjuntos.id"))
    hash_importacion: Mapped[str | None] = mapped_column(String(64), unique=True)
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=ahora_utc)
    actualizado_en: Mapped[datetime] = mapped_column(DateTime, default=ahora_utc, onupdate=ahora_utc)

    cuenta: Mapped[Cuenta] = relationship(foreign_keys=[cuenta_id])
    cuenta_destino: Mapped[Cuenta | None] = relationship(foreign_keys=[cuenta_destino_id])
    categoria: Mapped[Categoria | None] = relationship()
    meta: Mapped[MetaAhorro | None] = relationship()


# --- Fase 1b: presupuestos, metas de ahorro, deudas y suscripciones -----------

FRECUENCIAS = {"mensual": "Mensual", "anual": "Anual", "semanal": "Semanal"}
TIPOS_RECURRENTE = {
    "suscripcion": "Suscripción",
    "servicio": "Servicio básico",
    "deuda": "Deuda o préstamo",
    "otro": "Otro pago fijo",
}


class Presupuesto(Base):
    """Monto mensual para una categoría principal, vigente desde un mes (y opcionalmente hasta otro)."""

    __tablename__ = "presupuestos"
    __table_args__ = (CheckConstraint("monto > 0", name="monto_positivo"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    categoria_id: Mapped[int] = mapped_column(ForeignKey("categorias.id"), index=True)
    monto: Mapped[Decimal] = mapped_column(Dinero)
    desde_mes: Mapped[date] = mapped_column(Date)
    hasta_mes: Mapped[date | None] = mapped_column(Date)
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=ahora_utc)

    categoria: Mapped[Categoria] = relationship()


class MetaAhorro(Base):
    __tablename__ = "metas_ahorro"
    __table_args__ = (
        CheckConstraint("monto_objetivo > 0", name="objetivo_positivo"),
        CheckConstraint("estado IN ('activa','archivada')", name="estado_valido"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nombre: Mapped[str] = mapped_column(String(80))
    icono: Mapped[str | None] = mapped_column(String(16))
    monto_objetivo: Mapped[Decimal] = mapped_column(Dinero)
    fecha_objetivo: Mapped[date | None] = mapped_column(Date)
    saldo_inicial: Mapped[Decimal] = mapped_column(Dinero, default=Decimal("0.00"))
    estado: Mapped[str] = mapped_column(String(12), default="activa")
    orden: Mapped[int] = mapped_column(Integer, default=0)
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=ahora_utc)


class Recurrente(Base):
    """Suscripción, servicio o cuota de deuda que se repite."""

    __tablename__ = "recurrentes"
    __table_args__ = (
        CheckConstraint("monto > 0", name="monto_positivo"),
        CheckConstraint("frecuencia IN ('mensual','anual','semanal')", name="frecuencia_valida"),
        CheckConstraint("tipo IN ('suscripcion','servicio','deuda','otro')", name="tipo_valido"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nombre: Mapped[str] = mapped_column(String(80))
    tipo: Mapped[str] = mapped_column(String(15))
    monto: Mapped[Decimal] = mapped_column(Dinero)
    frecuencia: Mapped[str] = mapped_column(String(10), default="mensual")
    proxima_fecha: Mapped[date] = mapped_column(Date, index=True)
    dia: Mapped[int] = mapped_column(Integer)  # día original del vencimiento (31 → 28 en febrero → 31 en marzo)
    cuenta_id: Mapped[int] = mapped_column(ForeignKey("cuentas.id"))
    categoria_id: Mapped[int | None] = mapped_column(ForeignKey("categorias.id"))
    cuenta_destino_id: Mapped[int | None] = mapped_column(ForeignKey("cuentas.id"))  # deuda: tarjeta o préstamo
    dias_aviso: Mapped[int] = mapped_column(Integer, default=3)
    activa: Mapped[bool] = mapped_column(Boolean, default=True)
    # «¿Usaste esta suscripción este mes?»: meses seguidos respondidos que no
    meses_sin_uso: Mapped[int] = mapped_column(Integer, default=0)
    revision_uso: Mapped[date | None] = mapped_column(Date)
    nota: Mapped[str | None] = mapped_column(Text)
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=ahora_utc)

    cuenta: Mapped[Cuenta] = relationship(foreign_keys=[cuenta_id])
    cuenta_destino: Mapped[Cuenta | None] = relationship(foreign_keys=[cuenta_destino_id])
    categoria: Mapped[Categoria | None] = relationship()


# --- Fase 2: fotos de recibos, extracciones e importación ---------------------


class Adjunto(Base):
    __tablename__ = "adjuntos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nombre_archivo: Mapped[str] = mapped_column(String(200))
    tipo_mime: Mapped[str] = mapped_column(String(60))
    tamano: Mapped[int] = mapped_column(Integer)
    contenido: Mapped[bytes] = mapped_column(LargeBinary)
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=ahora_utc)


class Extraccion(Base):
    """Datos leídos de una foto, un XML o una fila importada, a la espera de que los confirmes."""

    __tablename__ = "extracciones"
    __table_args__ = (
        CheckConstraint("estado IN ('pendiente','lista','confirmada','descartada','error')", name="estado_valido"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fuente: Mapped[str] = mapped_column(String(15))  # foto | xml | importacion
    estado: Mapped[str] = mapped_column(String(12), default="pendiente", index=True)
    adjunto_id: Mapped[int | None] = mapped_column(ForeignKey("adjuntos.id"))
    datos: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    modelo: Mapped[str | None] = mapped_column(String(60))
    lote: Mapped[str | None] = mapped_column(String(40), index=True)
    movimiento_id: Mapped[int | None] = mapped_column(ForeignKey("movimientos.id"))
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=ahora_utc)

    adjunto: Mapped[Adjunto | None] = relationship()


class ReglaCategoria(Base):
    """Comercio → categoría, aprendida de tus confirmaciones."""

    __tablename__ = "reglas_categoria"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patron: Mapped[str] = mapped_column(String(120), unique=True)
    categoria_id: Mapped[int] = mapped_column(ForeignKey("categorias.id"))
    veces: Mapped[int] = mapped_column(Integer, default=1)
    actualizado_en: Mapped[datetime] = mapped_column(DateTime, default=ahora_utc, onupdate=ahora_utc)


# --- Fase 3: áreas, tareas, metas personales y registros ----------------------

PLANTILLAS_AREA = {
    "general": "General (tareas)",
    "fitness": "Fitness / gimnasio",
    "idioma": "Idioma",
    "habilidad": "Habilidad técnica",
    "postulaciones": "Postulaciones de trabajo",
    "becas": "Becas de maestría",
    "metas": "Metas personales",
}
ESTADOS_TAREA = {"pendiente": "Pendiente", "en_curso": "En curso", "hecha": "Hecha", "cancelada": "Cancelada"}
PRIORIDADES = {1: "Alta", 2: "Media", 3: "Baja"}
ESTADOS_POSTULACION = {
    "postulado": "Postulado",
    "entrevista": "Entrevista",
    "oferta": "Oferta",
    "rechazo": "Rechazo",
    "retirado": "Retirado",
}
ESTADOS_BECA = {
    "investigando": "Investigando",
    "preparando": "Preparando",
    "enviada": "Enviada",
    "aceptada": "Aceptada",
    "rechazada": "Rechazada",
}


class Area(Base):
    __tablename__ = "areas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nombre: Mapped[str] = mapped_column(String(60))
    icono: Mapped[str | None] = mapped_column(String(16))
    plantilla: Mapped[str] = mapped_column(String(20), default="general")
    config: Mapped[dict | None] = mapped_column(JSON)  # horas_semana, nivel_actual, nivel_objetivo…
    orden: Mapped[int] = mapped_column(Integer, default=0)
    activa: Mapped[bool] = mapped_column(Boolean, default=True)
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=ahora_utc)


class MetaPersonal(Base):
    __tablename__ = "metas_personales"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    area_id: Mapped[int | None] = mapped_column(ForeignKey("areas.id"))
    titulo: Mapped[str] = mapped_column(String(200))
    horizonte: Mapped[str] = mapped_column(String(12), default="trimestral")  # trimestral | anual
    periodo: Mapped[str] = mapped_column(String(10))  # 2026-T4 | 2027
    estado: Mapped[str] = mapped_column(String(12), default="activa")  # activa | cumplida | abandonada
    descripcion: Mapped[str | None] = mapped_column(Text)
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=ahora_utc)

    area: Mapped[Area | None] = relationship()


class Tarea(Base):
    __tablename__ = "tareas"
    __table_args__ = (
        CheckConstraint("estado IN ('pendiente','en_curso','hecha','cancelada')", name="estado_valido"),
        CheckConstraint("prioridad BETWEEN 1 AND 3", name="prioridad_valida"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    area_id: Mapped[int] = mapped_column(ForeignKey("areas.id"), index=True)
    titulo: Mapped[str] = mapped_column(String(200))
    descripcion: Mapped[str | None] = mapped_column(Text)
    fecha_limite: Mapped[date | None] = mapped_column(Date, index=True)
    prioridad: Mapped[int] = mapped_column(Integer, default=2)
    estado: Mapped[str] = mapped_column(String(12), default="pendiente")
    recurrencia: Mapped[str | None] = mapped_column(String(10))  # diaria | semanal | mensual
    padre_id: Mapped[int | None] = mapped_column(ForeignKey("tareas.id"))
    meta_personal_id: Mapped[int | None] = mapped_column(ForeignKey("metas_personales.id"))
    completada_en: Mapped[datetime | None] = mapped_column(DateTime)
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=ahora_utc)

    area: Mapped[Area] = relationship()


class Entrenamiento(Base):
    __tablename__ = "entrenamientos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fecha: Mapped[date] = mapped_column(Date, index=True)
    rutina: Mapped[str | None] = mapped_column(String(80))
    duracion_min: Mapped[int | None] = mapped_column(Integer)
    nota: Mapped[str | None] = mapped_column(Text)
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=ahora_utc)

    series: Mapped[list[Serie]] = relationship(cascade="all, delete-orphan", order_by="Serie.id")


class Serie(Base):
    __tablename__ = "series"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entrenamiento_id: Mapped[int] = mapped_column(ForeignKey("entrenamientos.id", ondelete="CASCADE"), index=True)
    ejercicio: Mapped[str] = mapped_column(String(80))
    repeticiones: Mapped[int] = mapped_column(Integer)
    peso: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))


class Rutina(Base):
    """Rutina semanal: un día y su lista de ejercicios (una línea por ejercicio, p. ej. «Sentadilla 4x8»)."""

    __tablename__ = "rutinas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nombre: Mapped[str] = mapped_column(String(80))
    dia_semana: Mapped[int] = mapped_column(Integer)  # 0 = lunes
    ejercicios: Mapped[str | None] = mapped_column(Text)


class SesionEstudio(Base):
    __tablename__ = "sesiones_estudio"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    area_id: Mapped[int] = mapped_column(ForeignKey("areas.id"), index=True)
    fecha: Mapped[date] = mapped_column(Date, index=True)
    minutos: Mapped[int] = mapped_column(Integer)
    tema: Mapped[str | None] = mapped_column(String(200))
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=ahora_utc)


class Postulacion(Base):
    __tablename__ = "postulaciones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    empresa: Mapped[str] = mapped_column(String(120))
    cargo: Mapped[str] = mapped_column(String(160))
    enlace: Mapped[str | None] = mapped_column(String(500))
    fecha_postulacion: Mapped[date] = mapped_column(Date)
    estado: Mapped[str] = mapped_column(String(12), default="postulado")
    proximo_paso: Mapped[str | None] = mapped_column(String(200))
    fecha_proximo_paso: Mapped[date | None] = mapped_column(Date)
    salario: Mapped[str | None] = mapped_column(String(60))
    notas: Mapped[str | None] = mapped_column(Text)
    actualizado_en: Mapped[datetime] = mapped_column(DateTime, default=ahora_utc, onupdate=ahora_utc)


class PostulacionEvento(Base):
    __tablename__ = "postulacion_eventos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    postulacion_id: Mapped[int] = mapped_column(ForeignKey("postulaciones.id", ondelete="CASCADE"), index=True)
    fecha: Mapped[date] = mapped_column(Date)
    estado: Mapped[str] = mapped_column(String(12))
    nota: Mapped[str | None] = mapped_column(String(300))


class Beca(Base):
    __tablename__ = "becas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    programa: Mapped[str] = mapped_column(String(160))
    universidad: Mapped[str | None] = mapped_column(String(160))
    pais: Mapped[str | None] = mapped_column(String(60))
    beca: Mapped[str | None] = mapped_column(String(160))
    enlace: Mapped[str | None] = mapped_column(String(500))
    fecha_limite: Mapped[date | None] = mapped_column(Date)
    estado: Mapped[str] = mapped_column(String(12), default="investigando")
    notas: Mapped[str | None] = mapped_column(Text)
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=ahora_utc)

    requisitos: Mapped[list[BecaRequisito]] = relationship(
        cascade="all, delete-orphan", order_by="BecaRequisito.orden"
    )


class BecaRequisito(Base):
    __tablename__ = "beca_requisitos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    beca_id: Mapped[int] = mapped_column(ForeignKey("becas.id", ondelete="CASCADE"), index=True)
    requisito: Mapped[str] = mapped_column(String(200))
    hecho: Mapped[bool] = mapped_column(Boolean, default=False)
    fecha_limite: Mapped[date | None] = mapped_column(Date)
    orden: Mapped[int] = mapped_column(Integer, default=0)


# --- Fase 4: asesor y avisos --------------------------------------------------


class Informe(Base):
    """Análisis semanal o mensual: los hechos calculados y el texto del asesor."""

    __tablename__ = "informes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tipo: Mapped[str] = mapped_column(String(12))  # semanal | mensual | revision
    periodo: Mapped[str] = mapped_column(String(20), index=True)
    hechos: Mapped[dict | list | None] = mapped_column(JSON)
    texto: Mapped[str | None] = mapped_column(Text)
    modelo: Mapped[str | None] = mapped_column(String(60))
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=ahora_utc)


class AvisoEnviado(Base):
    """Para no repetir un recordatorio (p. ej. «recurrente:5:2026-11-20»)."""

    __tablename__ = "avisos_enviados"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    clave: Mapped[str] = mapped_column(String(160), unique=True)
    enviado_en: Mapped[datetime] = mapped_column(DateTime, default=ahora_utc)


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
