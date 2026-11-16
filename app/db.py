"""Conexión a la base de datos: SQLite en local y en pruebas, PostgreSQL (Neon) en producción."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import MetaData, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import NullPool, StaticPool

from .config import ajustes

# Nombres de restricciones estables, para que las migraciones funcionen igual en SQLite y PostgreSQL
CONVENCION_NOMBRES = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=CONVENCION_NOMBRES)


def crear_motor(url: str) -> Engine:
    if url.startswith("sqlite"):
        opciones: dict = {"connect_args": {"check_same_thread": False}}
        if ":memory:" in url or url.rstrip("/").endswith("sqlite:") or url in ("sqlite://", "sqlite+pysqlite://"):
            opciones["poolclass"] = StaticPool
        motor = create_engine(url, **opciones)

        @event.listens_for(motor, "connect")
        def _activar_claves_foraneas(conexion_dbapi, _registro) -> None:
            cursor = conexion_dbapi.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        return motor
    # En Vercel cada invocación puede ser una instancia distinta: sin pool propio,
    # y en Neon se usa la cadena "pooled" (host con -pooler).
    return create_engine(url, poolclass=NullPool)


motor = crear_motor(ajustes.database_url)
SesionLocal = sessionmaker(bind=motor, expire_on_commit=False)


def obtener_bd() -> Iterator[Session]:
    bd = SesionLocal()
    try:
        yield bd
    finally:
        bd.close()
