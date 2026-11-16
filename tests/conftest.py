"""Configuración de pruebas: base SQLite en memoria y credenciales de prueba."""

from __future__ import annotations

import os
import re

import pyotp
from argon2 import PasswordHasher

# Debe definirse antes de importar la app (la configuración se lee al importar)
CONTRASENA_PRUEBA = "clave-de-prueba-123"
SECRETO_TOTP = pyotp.random_base32()
os.environ.update(
    {
        "ENTORNO": "pruebas",
        "DATABASE_URL": "sqlite+pysqlite:///:memory:",
        "SECRET_KEY": "s" * 48,
        "APP_PASSWORD_HASH": PasswordHasher().hash(CONTRASENA_PRUEBA),
        "TOTP_SECRET": SECRETO_TOTP,
    }
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, SesionLocal, motor  # noqa: E402
from app.main import app  # noqa: E402

PATRON_CSRF = re.compile(r'name="csrf-token" content="([^"]+)"|name="csrf_token" value="([^"]+)"')


def extraer_csrf(html: str) -> str:
    encontrado = PATRON_CSRF.search(html)
    assert encontrado, "La página no tiene token CSRF"
    return encontrado.group(1) or encontrado.group(2)


@pytest.fixture(autouse=True)
def base_limpia():
    Base.metadata.drop_all(motor)
    Base.metadata.create_all(motor)
    yield


@pytest.fixture
def bd():
    with SesionLocal() as sesion:
        yield sesion


@pytest.fixture
def cliente():
    with TestClient(app) as c:
        yield c


def iniciar_sesion(cliente: TestClient, contrasena: str = CONTRASENA_PRUEBA, codigo: str | None = None):
    pagina = cliente.get("/login")
    token = extraer_csrf(pagina.text)
    return cliente.post(
        "/login",
        data={
            "csrf_token": token,
            "password": contrasena,
            "codigo": codigo if codigo is not None else pyotp.TOTP(SECRETO_TOTP).now(),
            "siguiente": "/",
        },
        follow_redirects=False,
    )


@pytest.fixture
def cliente_auth(cliente):
    respuesta = iniciar_sesion(cliente)
    assert respuesta.status_code == 303, respuesta.text
    return cliente


@pytest.fixture
def csrf(cliente_auth) -> str:
    return extraer_csrf(cliente_auth.get("/mas").text)
