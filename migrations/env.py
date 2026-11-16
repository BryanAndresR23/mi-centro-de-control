"""Entorno de Alembic: toma la URL de la base de la configuración de la app."""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import models  # noqa: E402,F401  (registra las tablas)
from app.config import ajustes, normalizar_url_bd  # noqa: E402
from app.db import Base  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
# Neon recomienda migrar por la conexión directa (sin "-pooler"). La integración Neon de Vercel
# la entrega como DATABASE_URL_UNPOOLED; si no hay ninguna, se usa la normal.
_DIRECTA = next(
    (os.getenv(v) for v in ("DATABASE_URL_MIGRACIONES", "DATABASE_URL_UNPOOLED", "POSTGRES_URL_NON_POOLING") if os.getenv(v)),
    "",
)
URL = normalizar_url_bd(_DIRECTA) if _DIRECTA else ajustes.database_url


def run_migrations_offline() -> None:
    context.configure(
        url=URL,
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=URL.startswith("sqlite"),
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    motor = create_engine(URL, poolclass=pool.NullPool)
    with motor.connect() as conexion:
        context.configure(
            connection=conexion,
            target_metadata=target_metadata,
            render_as_batch=conexion.dialect.name == "sqlite",
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
