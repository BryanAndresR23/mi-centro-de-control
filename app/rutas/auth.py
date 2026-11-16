"""Inicio y cierre de sesión."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..config import ajustes
from ..db import obtener_bd
from ..finanzas.servicio import guardar_config, obtener_config
from ..seguridad import (
    contador_totp,
    destino_seguro,
    iniciar_sesion,
    ip_cliente,
    login_bloqueado,
    registrar_intento,
    verificar_csrf,
    verificar_password,
)
from ..web import render

router = APIRouter()


def _pagina_login(request: Request, siguiente: str, error: str | None = None, status_code: int = 200):
    return render(
        request,
        "login.html",
        {"siguiente": destino_seguro(siguiente), "usa_2fa": bool(ajustes.totp_secret), "error": error},
        status_code=status_code,
    )


@router.get("/login")
def formulario_login(request: Request, siguiente: str = "/"):
    if request.session.get("autenticado"):
        return RedirectResponse(destino_seguro(siguiente), status_code=303)
    return _pagina_login(request, siguiente)


@router.post("/login", dependencies=[Depends(verificar_csrf)])
async def login(request: Request, bd: Session = Depends(obtener_bd)):
    formulario = await request.form()
    siguiente = str(formulario.get("siguiente") or "/")
    ip = ip_cliente(request)

    if login_bloqueado(bd, ip):
        return _pagina_login(
            request, siguiente, "Demasiados intentos fallidos. Espera 15 minutos.", status_code=429
        )

    password_ok = verificar_password(ajustes.password_hash, str(formulario.get("password") or ""))
    contador = None
    codigo_ok = True
    if ajustes.totp_secret:
        contador = contador_totp(ajustes.totp_secret, str(formulario.get("codigo") or ""))
        ultimo = obtener_config(bd, "totp_ultimo_contador")
        # Un código ya usado no vale otra vez
        codigo_ok = contador is not None and (ultimo is None or contador > int(ultimo))

    exito = password_ok and codigo_ok
    if exito and contador is not None:
        guardar_config(bd, "totp_ultimo_contador", str(contador))
    registrar_intento(bd, ip, exito)  # hace commit

    if not exito:
        mensaje = "Contraseña o código incorrectos." if ajustes.totp_secret else "Contraseña incorrecta."
        return _pagina_login(request, siguiente, mensaje, status_code=401)

    iniciar_sesion(request)
    return RedirectResponse(destino_seguro(siguiente), status_code=303)


@router.post("/logout", dependencies=[Depends(verificar_csrf)])
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
