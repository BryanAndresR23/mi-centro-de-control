"""Plantillas Jinja y utilidades comunes de las páginas."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates

from . import VERSION
from .config import RAIZ
from .finanzas.montos import formato_porcentaje, formato_usd
from .seguridad import token_csrf
from .tiempo import fecha_corta, fecha_larga, nombre_mes

plantillas = Jinja2Templates(directory=str(RAIZ / "app" / "templates"))
plantillas.env.filters["usd"] = formato_usd
plantillas.env.filters["pct"] = formato_porcentaje
plantillas.env.filters["fecha_corta"] = fecha_corta
plantillas.env.filters["fecha_larga"] = fecha_larga
plantillas.env.globals["csrf_token"] = token_csrf
plantillas.env.globals["nombre_mes"] = nombre_mes
plantillas.env.globals["VERSION"] = VERSION


def avisar(request: Request, texto: str, tipo: str = "ok") -> None:
    """Mensaje que se muestra una vez en la siguiente página."""
    avisos = request.session.get("avisos", [])
    avisos.append({"tipo": tipo, "texto": texto})
    request.session["avisos"] = avisos


def render(request: Request, nombre: str, contexto: dict[str, Any] | None = None, status_code: int = 200):
    datos = dict(contexto or {})
    datos.setdefault("avisos", request.session.pop("avisos", []) if "session" in request.scope else [])
    datos.setdefault("seccion", "")
    return plantillas.TemplateResponse(request, nombre, datos, status_code=status_code)


def es_htmx(request: Request) -> bool:
    return request.headers.get("hx-request") == "true"
