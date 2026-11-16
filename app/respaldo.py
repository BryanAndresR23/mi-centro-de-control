"""Respaldo completo (ZIP con JSON + un CSV por tabla) y restauración desde ese JSON.

Es genérico: recorre todas las tablas del modelo, así las tablas de fases futuras
entran en el respaldo sin tocar este archivo.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Integer, Table, delete, insert, select, text, update
from sqlalchemy.orm import Session

from . import models  # noqa: F401  (registra las tablas en Base.metadata)
from .db import Base
from .tiempo import ahora_utc

FORMATO = "mi-centro-de-control/respaldo"
VERSION_FORMATO = 1
TABLAS_EXCLUIDAS = {"intentos_login"}
# Valores de config que pertenecen a esta instalación y no se sobrescriben al restaurar
CONFIG_LOCAL = {"totp_ultimo_contador"}
TAMANO_MAXIMO = 25 * 1024 * 1024


class RespaldoInvalido(ValueError):
    pass


def tablas_respaldables() -> list[Table]:
    return [t for t in Base.metadata.sorted_tables if t.name not in TABLAS_EXCLUIDAS]


def _a_json(valor: Any) -> Any:
    if isinstance(valor, Decimal):
        return str(valor)
    if isinstance(valor, (date, datetime)):
        return valor.isoformat()
    return valor


def exportar(bd: Session) -> dict:
    tablas: dict[str, list[dict]] = {}
    for tabla in tablas_respaldables():
        filas = bd.execute(select(tabla).order_by(*tabla.primary_key.columns)).mappings().all()
        tablas[tabla.name] = [{k: _a_json(v) for k, v in fila.items()} for fila in filas]
    return {
        "formato": FORMATO,
        "version": VERSION_FORMATO,
        "exportado_en": ahora_utc().isoformat(timespec="seconds") + "Z",
        "tablas": tablas,
    }


LEEME = """Respaldo de Mi Centro de Control
================================

- datos.json: respaldo completo. Es el archivo que se usa para restaurar
  (Más > Respaldo > Restaurar).
- *.csv: una tabla por archivo (UTF-8, separado por comas, punto decimal),
  para abrir en Excel o Power BI.

Guarda este ZIP en un lugar seguro: contiene toda tu información financiera.
"""


def a_zip(datos: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archivo:
        archivo.writestr("datos.json", json.dumps(datos, ensure_ascii=False, indent=1))
        for nombre, filas in datos["tablas"].items():
            columnas = [c.name for c in Base.metadata.tables[nombre].columns]
            salida = io.StringIO()
            escritor = csv.writer(salida, lineterminator="\r\n")
            escritor.writerow(columnas)
            for fila in filas:
                escritor.writerow(["" if fila.get(c) is None else fila.get(c) for c in columnas])
            # Con BOM para que Excel reconozca las tildes
            archivo.writestr(f"{nombre}.csv", "﻿" + salida.getvalue())
        archivo.writestr("LEEME.txt", LEEME)
    return buffer.getvalue()


def leer_archivo(contenido: bytes) -> dict:
    """Acepta el ZIP de respaldo o directamente su datos.json."""
    if len(contenido) > TAMANO_MAXIMO:
        raise RespaldoInvalido("El archivo es demasiado grande.")
    if contenido[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(contenido)) as archivo:
                contenido = archivo.read("datos.json")
        except (zipfile.BadZipFile, KeyError) as exc:
            raise RespaldoInvalido("El ZIP no contiene un datos.json válido.") from exc
    try:
        datos = json.loads(contenido.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RespaldoInvalido("El archivo no es un JSON válido.") from exc
    if not isinstance(datos, dict) or datos.get("formato") != FORMATO:
        raise RespaldoInvalido("Este archivo no es un respaldo de Mi Centro de Control.")
    if not isinstance(datos.get("version"), int) or datos["version"] > VERSION_FORMATO:
        raise RespaldoInvalido("El respaldo es de una versión más nueva de la app.")
    if not isinstance(datos.get("tablas"), dict):
        raise RespaldoInvalido("El respaldo no tiene tablas.")
    return datos


def _convertir(columna, valor: Any) -> Any:
    if valor is None:
        return None
    try:
        tipo = columna.type.python_type
    except NotImplementedError:
        return valor
    try:
        if tipo is Decimal:
            return Decimal(str(valor))
        if tipo is datetime:
            return datetime.fromisoformat(str(valor).rstrip("Z"))
        if tipo is date:
            return date.fromisoformat(str(valor))
        if tipo is bool:
            return valor if isinstance(valor, bool) else str(valor).lower() in ("1", "true")
        if tipo is int:
            return int(valor)
        return str(valor)
    except (ValueError, ArithmeticError) as exc:
        raise RespaldoInvalido(f"Valor inválido en {columna.table.name}.{columna.name}: {valor!r}") from exc


def _reiniciar_secuencias(bd: Session, tablas: list[Table]) -> None:
    """En PostgreSQL, tras insertar con ids explícitos, los contadores deben seguir desde el máximo."""
    if bd.get_bind().dialect.name != "postgresql":
        return
    for tabla in tablas:
        clave = list(tabla.primary_key.columns)
        if len(clave) == 1 and isinstance(clave[0].type, Integer):
            col = clave[0].name
            bd.execute(
                text(
                    f"SELECT setval(pg_get_serial_sequence('{tabla.name}', '{col}'), "
                    f"COALESCE((SELECT MAX({col}) FROM {tabla.name}), 0) + 1, false)"
                )
            )


def restaurar(bd: Session, datos: dict) -> dict[str, int]:
    """Reemplaza TODOS los datos por los del respaldo, en una sola transacción."""
    tablas = tablas_respaldables()
    nombres = {t.name for t in tablas}
    recibidas: dict = datos["tablas"]
    desconocidas = set(recibidas) - nombres
    if desconocidas:
        raise RespaldoInvalido(f"Tablas desconocidas en el respaldo: {', '.join(sorted(desconocidas))}")

    tabla_config = Base.metadata.tables["config"]
    config_local = {
        fila.clave: fila.valor
        for fila in bd.execute(select(tabla_config).where(tabla_config.c.clave.in_(CONFIG_LOCAL)))
    }

    conteo: dict[str, int] = {}
    try:
        for tabla in reversed(tablas):
            bd.execute(delete(tabla))

        autorreferencias: list[tuple[Table, Any, str, Any]] = []
        for tabla in tablas:
            filas = recibidas.get(tabla.name) or []
            if not isinstance(filas, list):
                raise RespaldoInvalido(f"La tabla {tabla.name} no tiene el formato esperado.")
            columnas = {c.name: c for c in tabla.columns}
            columnas_ref = [fk.parent.name for fk in tabla.foreign_keys if fk.column.table is tabla]
            clave = list(tabla.primary_key.columns)[0].name
            presentes: set[str] = set()
            for fila in filas:
                if not isinstance(fila, dict):
                    raise RespaldoInvalido(f"Fila inválida en {tabla.name}.")
                extra = set(fila) - set(columnas)
                if extra:
                    raise RespaldoInvalido(f"Columnas desconocidas en {tabla.name}: {', '.join(sorted(extra))}")
                presentes |= set(fila)

            preparadas = []
            for fila in filas:
                if tabla.name == "config" and fila.get("clave") in CONFIG_LOCAL:
                    continue
                lista = {c: _convertir(columnas[c], fila.get(c)) for c in presentes}
                # Las referencias a la misma tabla (categoría padre) se aplican al final
                for col in columnas_ref:
                    if lista.get(col) is not None:
                        autorreferencias.append((tabla, lista[clave], col, lista[col]))
                        lista[col] = None
                preparadas.append(lista)
            if preparadas:
                bd.execute(insert(tabla), preparadas)
            conteo[tabla.name] = len(preparadas)

        for tabla, valor_clave, col, valor in autorreferencias:
            col_clave = list(tabla.primary_key.columns)[0]
            bd.execute(update(tabla).where(col_clave == valor_clave).values({col: valor}))

        for clave_cfg, valor_cfg in config_local.items():
            bd.execute(insert(tabla_config).values(clave=clave_cfg, valor=valor_cfg))

        _reiniciar_secuencias(bd, tablas)
        bd.commit()
    except Exception:
        bd.rollback()
        raise
    return conteo
