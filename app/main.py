"""Aplicación FastAPI: middleware de seguridad, sesión y rutas."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from .config import RAIZ, ajustes
from .rutas import (
    asesor,
    auth,
    categorias,
    cuentas,
    inicio,
    movimientos,
    pendientes,
    planificacion,
    recibos,
    respaldo,
)
from .seguridad import DURACION_SESION_SEG, NoAutenticado
from .web import es_htmx, plantillas, render

POLITICA_CSP = (
    "default-src 'self'; "
    "script-src 'self' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)


def crear_app() -> FastAPI:
    app = FastAPI(title="Mi Centro de Control", docs_url=None, redoc_url=None, openapi_url=None)
    errores_config = ajustes.errores()

    @app.middleware("http")
    async def cabeceras_seguridad(request: Request, call_next):
        if errores_config and not request.url.path.startswith("/static"):
            html = plantillas.get_template("config_incompleta.html").render(
                errores=errores_config, produccion=ajustes.es_produccion
            )
            respuesta: Response = HTMLResponse(html, status_code=503)
        else:
            respuesta = await call_next(request)
        respuesta.headers["Content-Security-Policy"] = POLITICA_CSP
        respuesta.headers["X-Content-Type-Options"] = "nosniff"
        respuesta.headers["X-Frame-Options"] = "DENY"
        respuesta.headers["Referrer-Policy"] = "same-origin"
        respuesta.headers["Permissions-Policy"] = "camera=(self), geolocation=(), microphone=()"
        if ajustes.es_produccion:
            respuesta.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if not request.url.path.startswith("/static"):
            respuesta.headers.setdefault("Cache-Control", "no-store")
        return respuesta

    app.add_middleware(
        SessionMiddleware,
        secret_key=ajustes.secret_key or "configuracion-incompleta-" + "x" * 32,
        session_cookie="mcc_sesion",
        max_age=DURACION_SESION_SEG,
        same_site="lax",
        https_only=ajustes.es_produccion,
    )

    @app.exception_handler(NoAutenticado)
    async def _no_autenticado(request: Request, _exc: NoAutenticado):
        if es_htmx(request):
            return Response(status_code=204, headers={"HX-Redirect": "/login"})
        siguiente = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        destino = "/login" if siguiente in ("/", "") else f"/login?siguiente={siguiente}"
        return RedirectResponse(destino, status_code=303)

    @app.exception_handler(StarletteHTTPException)
    async def _error_http(request: Request, exc: StarletteHTTPException):
        titulos = {403: "Acceso denegado", 404: "No encontrado", 405: "Acción no permitida"}
        return render(
            request,
            "error.html",
            {"titulo": titulos.get(exc.status_code, "Error"), "mensaje": exc.detail, "codigo": exc.status_code},
            status_code=exc.status_code,
        )

    @app.exception_handler(RequestValidationError)
    async def _error_validacion(request: Request, _exc: RequestValidationError):
        return render(
            request,
            "error.html",
            {"titulo": "Datos inválidos", "mensaje": "La dirección o el formulario tienen datos inválidos.", "codigo": 422},
            status_code=422,
        )

    app.mount("/static", StaticFiles(directory=str(RAIZ / "app" / "static")), name="static")
    for modulo in (auth, inicio, movimientos, cuentas, categorias, respaldo):
        app.include_router(modulo.router)
    for router in (planificacion.presupuestos, planificacion.metas, planificacion.recurrentes,
                   recibos.recibos, recibos.importar, pendientes.hoy_router, pendientes.router,
                   asesor.router, asesor.cron):
        app.include_router(router)
    return app


app = crear_app()
