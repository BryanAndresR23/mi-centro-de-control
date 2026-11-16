"""Genera tus credenciales de acceso: hash de la contraseña, secreto 2FA y SECRET_KEY.

Uso (desde la carpeta del proyecto, con el entorno virtual):
  python scripts/crear_credenciales.py            Tu contraseña real + Google Authenticator.
                                                  Guarda los valores en .env y los muestra
                                                  para copiarlos a Vercel.
  python scripts/crear_credenciales.py --demo     Contraseña de prueba del README, sin 2FA.
                                                  Solo para probar en tu PC.
Opciones:
  --no-guardar   Solo muestra los valores; no modifica .env
  --sin-2fa      No configura 2FA (solo para desarrollo; producción lo exige)
"""

from __future__ import annotations

import argparse
import getpass
import secrets
import sys
import webbrowser
from pathlib import Path

from argon2 import PasswordHasher

RAIZ = Path(__file__).resolve().parent.parent
ARCHIVO_ENV = RAIZ / ".env"
ARCHIVO_QR = RAIZ / "credenciales_qr.svg"
CONTRASENA_DEMO = "demo-centro-2026"  # documentada en el README; nunca la uses en producción
LARGO_MINIMO = 12


def actualizar_env(valores: dict[str, str]) -> None:
    """Reemplaza o agrega las claves en .env, sin tocar el resto."""
    lineas = ARCHIVO_ENV.read_text(encoding="utf-8").splitlines() if ARCHIVO_ENV.exists() else []
    pendientes = dict(valores)
    nuevas = []
    for linea in lineas:
        clave = linea.split("=", 1)[0].strip()
        if clave in pendientes:
            # Comillas simples: python-dotenv no interpreta los "$" del hash
            nuevas.append(f"{clave}='{pendientes.pop(clave)}'")
        else:
            nuevas.append(linea)
    nuevas.extend(f"{clave}='{valor}'" for clave, valor in pendientes.items())
    ARCHIVO_ENV.write_text("\n".join(nuevas) + "\n", encoding="utf-8")


def pedir_contrasena() -> str:
    while True:
        primera = getpass.getpass(f"Contraseña nueva (mínimo {LARGO_MINIMO} caracteres): ")
        if len(primera) < LARGO_MINIMO:
            print(f"Muy corta. Usa al menos {LARGO_MINIMO} caracteres (una frase sirve).")
            continue
        if getpass.getpass("Repítela: ") != primera:
            print("No coinciden. Intenta de nuevo.")
            continue
        return primera


def configurar_2fa() -> str:
    import pyotp

    secreto = pyotp.random_base32()
    uri = pyotp.TOTP(secreto).provisioning_uri(name="mi-centro-de-control", issuer_name="Mi Centro de Control")
    try:
        import qrcode
        import qrcode.image.svg

        qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage).save(str(ARCHIVO_QR))
        print(f"\nAbre {ARCHIVO_QR.name} y escanéalo con Google Authenticator (botón +, «Escanear un código QR»).")
        webbrowser.open(ARCHIVO_QR.as_uri())
    except ImportError:
        print("\n(Instala requirements-dev.txt para ver el código QR.)")
    print(f"Si no puedes escanear, en la app elige «Ingresar clave de configuración» y escribe:\n  {secreto}\n")

    totp = pyotp.TOTP(secreto)
    for _ in range(3):
        codigo = input("Escribe el código de 6 dígitos que muestra la app: ").replace(" ", "")
        if totp.verify(codigo, valid_window=1):
            print("Código correcto: 2FA configurado.")
            break
        print("Ese código no coincide. Revisa la hora del celular e intenta de nuevo.")
    else:
        sys.exit("No se pudo verificar el código. Vuelve a ejecutar el script.")
    if ARCHIVO_QR.exists():
        ARCHIVO_QR.unlink()  # el QR contiene el secreto: no se deja en disco
    return secreto


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--no-guardar", action="store_true")
    parser.add_argument("--sin-2fa", action="store_true")
    args = parser.parse_args()

    hasher = PasswordHasher()
    if args.demo:
        contrasena = CONTRASENA_DEMO
        secreto = ""
    else:
        contrasena = pedir_contrasena()
        secreto = "" if args.sin_2fa else configurar_2fa()

    valores = {
        "APP_PASSWORD_HASH": hasher.hash(contrasena),
        "TOTP_SECRET": secreto,
        "SECRET_KEY": secrets.token_urlsafe(48),
    }

    if not args.no_guardar:
        actualizar_env(valores)
        print(f"\nGuardado en {ARCHIVO_ENV}.")
    if args.demo:
        print("Modo demo listo: entra con la contraseña de prueba del README.")
        return

    bloque = "ENTORNO=produccion\n" + "".join(f"{clave}={valor}\n" for clave, valor in valores.items() if valor)
    print("\nPara Vercel (Settings → Environment Variables), estas son las variables, sin comillas:\n")
    print(bloque)
    try:
        import subprocess

        subprocess.run("clip", input=bloque.encode("ascii"), check=True, shell=True)
        print("También quedaron copiadas en el portapapeles: en Vercel, pégalas en el campo «Key»")
        print("de una variable nueva y se separan solas.")
    except Exception:
        pass
    print("\nNo compartas estos valores. Si los pierdes, vuelve a ejecutar este script.")


if __name__ == "__main__":
    main()
