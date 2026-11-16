import pyotp

from app.config import Ajustes
from app.seguridad import MAX_INTENTOS_FALLIDOS, contador_totp, destino_seguro

from .conftest import SECRETO_TOTP, extraer_csrf, iniciar_sesion


def test_sin_sesion_redirige_al_login(cliente):
    for ruta in ("/", "/movimientos", "/cuentas", "/respaldo/descargar"):
        r = cliente.get(ruta, follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"].startswith("/login")


def test_login_correcto(cliente):
    r = iniciar_sesion(cliente)
    assert r.status_code == 303
    assert cliente.get("/").status_code == 200


def test_contrasena_incorrecta(cliente):
    r = iniciar_sesion(cliente, contrasena="otra-cosa")
    assert r.status_code == 401
    assert cliente.get("/", follow_redirects=False).status_code == 303


def test_codigo_2fa_incorrecto(cliente):
    codigo_malo = "000000" if pyotp.TOTP(SECRETO_TOTP).now() != "000000" else "111111"
    r = iniciar_sesion(cliente, codigo=codigo_malo)
    assert r.status_code == 401


def test_un_codigo_2fa_no_se_puede_reutilizar(cliente):
    codigo = pyotp.TOTP(SECRETO_TOTP).now()
    assert iniciar_sesion(cliente, codigo=codigo).status_code == 303
    token = extraer_csrf(cliente.get("/mas").text)
    cliente.post("/logout", data={"csrf_token": token})
    assert iniciar_sesion(cliente, codigo=codigo).status_code == 401


def test_bloqueo_tras_intentos_fallidos(cliente):
    for _ in range(MAX_INTENTOS_FALLIDOS):
        assert iniciar_sesion(cliente, contrasena="mala").status_code == 401
    # Bloqueado incluso con la contraseña correcta
    assert iniciar_sesion(cliente).status_code == 429


def test_post_sin_csrf_es_rechazado(cliente_auth):
    r = cliente_auth.post("/cuentas/nueva", data={"nombre": "X", "tipo": "efectivo"})
    assert r.status_code == 403


def test_logout(cliente_auth, csrf):
    r = cliente_auth.post("/logout", data={"csrf_token": csrf}, follow_redirects=False)
    assert r.status_code == 303
    assert cliente_auth.get("/", follow_redirects=False).status_code == 303


def test_cabeceras_de_seguridad(cliente):
    r = cliente.get("/login")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store"


def test_destino_seguro_evita_redirecciones_externas():
    assert destino_seguro("/movimientos?mes=2026-01") == "/movimientos?mes=2026-01"
    assert destino_seguro("https://malo.com") == "/"
    assert destino_seguro("//malo.com") == "/"
    assert destino_seguro("") == "/"


def test_contador_totp_acepta_desfase_de_30_segundos():
    totp = pyotp.TOTP(SECRETO_TOTP)
    ahora = 1_800_000_000
    assert contador_totp(SECRETO_TOTP, totp.at(ahora - 30), ahora) is not None
    assert contador_totp(SECRETO_TOTP, totp.at(ahora - 120), ahora) is None
    assert contador_totp(SECRETO_TOTP, "12345", ahora) is None


def test_produccion_exige_2fa_postgres_y_clave_larga():
    ajustes = Ajustes("produccion", "sqlite:///x.db", "corta", "", "")
    errores = " ".join(ajustes.errores())
    for esperado in ("APP_PASSWORD_HASH", "SECRET_KEY", "TOTP_SECRET", "DATABASE_URL"):
        assert esperado in errores
    completo = Ajustes("produccion", "postgresql+psycopg://u:p@h/db", "k" * 40, "$argon2id$...", "ABC")
    assert completo.errores() == []
