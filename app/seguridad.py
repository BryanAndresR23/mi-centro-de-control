"""Contraseña (Argon2), código 2FA (TOTP), límite de intentos, sesión y CSRF."""

from __future__ import annotations

import hmac
import secrets
import time
from datetime import timedelta
from urllib.parse import urlsplit

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import HTTPException, Request
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .config import ajustes
from .models import IntentoLogin
from .tiempo import ahora_utc

_hasher = PasswordHasher()

MAX_INTENTOS_FALLIDOS = 5
VENTANA_BLOQUEO = timedelta(minutes=15)
DURACION_SESION_SEG = 30 * 24 * 3600


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verificar_password(hash_guardado: str, password: str) -> bool:
    if not hash_guardado or not password:
        return False
    try:
        return _hasher.verify(hash_guardado, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def contador_totp(secreto: str, codigo: str, ahora: float | None = None) -> int | None:
    """Devuelve el contador del código si es válido (acepta ±30 s de desfase), o None.

    Guardar el último contador usado impide reutilizar el mismo código dos veces.
    """
    codigo = (codigo or "").replace(" ", "")
    if not (len(codigo) == 6 and codigo.isdigit()):
        return None
    totp = pyotp.TOTP(secreto)
    base = int((ahora if ahora is not None else time.time()) // totp.interval)
    for delta in (0, -1, 1):
        if hmac.compare_digest(totp.generate_otp(base + delta), codigo):
            return base + delta
    return None


def ip_cliente(request: Request) -> str:
    # En Vercel la IP real llega en x-forwarded-for (Vercel la sobrescribe, no la inventa el cliente)
    if ajustes.es_produccion:
        reenviada = request.headers.get("x-forwarded-for", "")
        if reenviada:
            return reenviada.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "desconocida")[:64]


def login_bloqueado(bd: Session, ip: str) -> bool:
    desde = ahora_utc() - VENTANA_BLOQUEO
    fallidos = bd.scalar(
        select(func.count())
        .select_from(IntentoLogin)
        .where(IntentoLogin.ip == ip, IntentoLogin.exito.is_(False), IntentoLogin.momento >= desde)
    )
    return (fallidos or 0) >= MAX_INTENTOS_FALLIDOS


def registrar_intento(bd: Session, ip: str, exito: bool) -> None:
    bd.add(IntentoLogin(ip=ip, exito=exito, momento=ahora_utc()))
    if exito:
        # Limpieza: los fallidos de esta IP ya no cuentan y se borra historial viejo
        bd.execute(delete(IntentoLogin).where(IntentoLogin.ip == ip, IntentoLogin.exito.is_(False)))
        bd.execute(delete(IntentoLogin).where(IntentoLogin.momento < ahora_utc() - timedelta(days=30)))
    bd.commit()


# --- Sesión -----------------------------------------------------------------


class NoAutenticado(Exception):
    pass


def iniciar_sesion(request: Request) -> None:
    request.session.clear()
    request.session.update(
        {"autenticado": True, "inicio": int(time.time()), "csrf": secrets.token_urlsafe(32)}
    )


def requiere_login(request: Request) -> None:
    sesion = request.session
    if not sesion.get("autenticado"):
        raise NoAutenticado()
    if time.time() - float(sesion.get("inicio", 0)) > DURACION_SESION_SEG:
        sesion.clear()
        raise NoAutenticado()


def destino_seguro(siguiente: str | None) -> str:
    """Solo rutas internas, para que /login?siguiente= no redirija a otro sitio."""
    if not siguiente:
        return "/"
    partes = urlsplit(siguiente)
    if partes.scheme or partes.netloc or not siguiente.startswith("/") or siguiente.startswith("//"):
        return "/"
    return siguiente


# --- CSRF -------------------------------------------------------------------


def token_csrf(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


async def verificar_csrf(request: Request) -> None:
    """Todo POST debe traer el token de la sesión (campo oculto o cabecera X-CSRF-Token)."""
    enviado = request.headers.get("x-csrf-token")
    if not enviado:
        formulario = await request.form()
        enviado = formulario.get("csrf_token")
    esperado = request.session.get("csrf")
    if not esperado or not enviado or not hmac.compare_digest(str(enviado), str(esperado)):
        raise HTTPException(status_code=403, detail="La página caducó. Recárgala e inténtalo de nuevo.")
