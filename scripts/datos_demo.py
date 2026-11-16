"""Carga 12 meses de datos de ejemplo en la base LOCAL (borra lo que haya en ella).

Uso: python scripts/datos_demo.py
"""

from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from alembic import command  # noqa: E402
from alembic.config import Config as ConfigAlembic  # noqa: E402

from app.config import ajustes  # noqa: E402

if not ajustes.database_url.startswith("sqlite"):
    sys.exit("Por seguridad, los datos de ejemplo solo se cargan en la base local (SQLite), nunca en Neon.")

command.upgrade(ConfigAlembic(str(RAIZ / "alembic.ini")), "head")

from app.db import SesionLocal  # noqa: E402
from app.demo import cargar_demo  # noqa: E402
from app.tiempo import hoy  # noqa: E402

with SesionLocal() as bd:
    resumen = cargar_demo(bd, hoy())
print(f"Datos de ejemplo cargados: {resumen['cuentas']} cuentas, {resumen['categorias']} categorías, "
      f"{resumen['movimientos']} movimientos.")
