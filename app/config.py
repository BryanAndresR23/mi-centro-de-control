"""Configuración leída de variables de entorno (.env en local, panel de Vercel en producción)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent

# Las variables ya definidas en el entorno (Vercel, pruebas) tienen prioridad sobre .env
load_dotenv(RAIZ / ".env", override=False)


def normalizar_url_bd(url: str) -> str:
    """Neon entrega `postgresql://...`; SQLAlchemy necesita el driver psycopg explícito."""
    url = url.strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


@dataclass(frozen=True)
class Ajustes:
    entorno: str
    database_url: str
    secret_key: str
    password_hash: str
    totp_secret: str

    @property
    def es_produccion(self) -> bool:
        return self.entorno == "produccion"

    def errores(self) -> list[str]:
        """Variables que faltan o no sirven. Nunca incluye los valores."""
        errores: list[str] = []
        if not self.password_hash:
            errores.append("APP_PASSWORD_HASH")
        if len(self.secret_key) < 32:
            errores.append("SECRET_KEY (mínimo 32 caracteres)")
        if self.es_produccion:
            if not self.totp_secret:
                errores.append("TOTP_SECRET (obligatorio en producción)")
            if self.database_url.startswith("sqlite"):
                errores.append("DATABASE_URL (en producción debe ser PostgreSQL)")
        return errores


def cargar_ajustes() -> Ajustes:
    entorno_defecto = "produccion" if os.getenv("VERCEL") else "desarrollo"
    url_defecto = f"sqlite:///{(RAIZ / 'datos_locales.db').as_posix()}"
    return Ajustes(
        entorno=(os.getenv("ENTORNO") or entorno_defecto).strip().lower(),
        database_url=normalizar_url_bd(os.getenv("DATABASE_URL") or url_defecto),
        secret_key=os.getenv("SECRET_KEY", ""),
        password_hash=os.getenv("APP_PASSWORD_HASH", "").strip(),
        totp_secret=os.getenv("TOTP_SECRET", "").replace(" ", "").upper(),
    )


ajustes = cargar_ajustes()
