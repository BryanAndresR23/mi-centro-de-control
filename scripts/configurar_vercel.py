"""Ventana para publicar Mi Centro de Control en Vercel sin usar el panel web.

Uso: python scripts/configurar_vercel.py

En la ventana:
  1. Pegas un token de Vercel (Account Settings → Tokens).
  2. Pegas la cadena de conexión de tu base Neon (console.neon.tech → Connect).
  3. Escribes tu contraseña y escaneas el código QR con Google Authenticator.
Después la ventana crea (o reutiliza) el proyecto en Vercel ligado a tu repo de GitHub,
guarda las variables de entorno cifradas y lanza el despliegue de producción.

Los secretos solo viven en esta ventana y se envían directo a Vercel: no se imprimen,
no se guardan en disco y no aparecen en el registro (configurar_vercel.log).
"""

from __future__ import annotations

import json
import secrets
import threading
import time
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

import pyotp
from argon2 import PasswordHasher

RAIZ = Path(__file__).resolve().parent.parent
REGISTRO = RAIZ / "configurar_vercel.log"
ARCHIVO_URL = RAIZ / "produccion_url.txt"

PROYECTO = "mi-centro-de-control"
DUENO_GITHUB = "BryanAndresR23"
API = "https://api.vercel.com"
URL_TOKEN = "https://vercel.com/account/settings/tokens"
URL_NEON = "https://console.neon.tech"
URL_APP_GITHUB = "https://github.com/apps/vercel/installations/new"
LARGO_MINIMO = 12


def registrar(mensaje: str) -> None:
    with REGISTRO.open("a", encoding="utf-8") as archivo:
        archivo.write(f"{time.strftime('%H:%M:%S')} {mensaje}\n")


class ErrorVercel(Exception):
    def __init__(self, estado: int, cuerpo: dict):
        self.estado = estado
        self.cuerpo = cuerpo
        error = cuerpo.get("error") or {}
        super().__init__(f"HTTP {estado}: {error.get('message') or cuerpo}")


def vercel(metodo: str, ruta: str, token: str, cuerpo=None, query: dict | None = None) -> dict:
    url = API + ruta + (("?" + urllib.parse.urlencode(query)) if query else "")
    pedido = urllib.request.Request(
        url,
        method=metodo,
        data=json.dumps(cuerpo).encode() if cuerpo is not None else None,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(pedido, timeout=60) as respuesta:
            return json.loads(respuesta.read() or b"{}")
    except urllib.error.HTTPError as error:
        try:
            datos = json.loads(error.read() or b"{}")
        except json.JSONDecodeError:
            datos = {}
        raise ErrorVercel(error.code, datos) from None


def urls_neon(cadena: str) -> tuple[str, str]:
    """(con pooler, directa). Neon distingue ambas por el sufijo -pooler del primer segmento del host."""
    partes = urllib.parse.urlsplit(cadena.strip())
    if partes.scheme not in ("postgres", "postgresql") or not partes.hostname:
        raise ValueError("La cadena de Neon debe empezar por postgresql://")
    host = partes.hostname
    primero, _, resto = host.partition(".")
    if primero.endswith("-pooler"):
        host_pool, host_directo = host, f"{primero[: -len('-pooler')]}.{resto}"
    else:
        host_pool, host_directo = f"{primero}-pooler.{resto}", host

    def con_host(nuevo: str) -> str:
        credenciales, _, _ = partes.netloc.rpartition("@")
        puerto = f":{partes.port}" if partes.port else ""
        return urllib.parse.urlunsplit(partes._replace(netloc=f"{credenciales}@{nuevo}{puerto}"))

    if ".neon.tech" not in host:
        return cadena.strip(), cadena.strip()  # no es Neon: se usa igual para todo
    return con_host(host_pool), con_host(host_directo)


class Ventana:
    def __init__(self) -> None:
        self.raiz = tk.Tk()
        self.raiz.title("Publicar Mi Centro de Control")
        self.raiz.attributes("-topmost", True)
        self.raiz.geometry("660x1000")
        self.secreto_totp = ""
        fuente = ("Segoe UI", 10)
        negrita = ("Segoe UI", 10, "bold")

        def seccion(texto: str) -> None:
            tk.Label(self.raiz, text=texto, font=negrita, anchor="w").pack(fill="x", padx=16, pady=(12, 2))

        def campo(etiqueta: str, oculto: bool = True) -> tk.Entry:
            fila = tk.Frame(self.raiz)
            fila.pack(fill="x", padx=16, pady=2)
            tk.Label(fila, text=etiqueta, font=fuente, width=22, anchor="w").pack(side="left")
            entrada = tk.Entry(fila, show="*" if oculto else "", font=fuente)
            entrada.pack(side="left", fill="x", expand=True)
            return entrada

        def boton_enlace(texto: str, url: str) -> None:
            tk.Button(self.raiz, text=texto, command=lambda: webbrowser.open(url)).pack(anchor="w", padx=16, pady=2)

        seccion("1. Token de Vercel")
        tk.Label(self.raiz, font=("Segoe UI", 9), justify="left", anchor="w", text=(
            "En Vercel: Create Token → nombre «mi-centro», alcance tu cuenta personal, vencimiento corto → copia el token."
        )).pack(fill="x", padx=16)
        boton_enlace("Abrir la página de tokens de Vercel", URL_TOKEN)
        self.token = campo("Token de Vercel")

        seccion("2. Base de datos Neon (gratis)")
        tk.Label(self.raiz, font=("Segoe UI", 9), justify="left", anchor="w", text=(
            "En Neon: regístrate (puede ser con GitHub) → crea el proyecto «mi-centro-de-control» en la región\n"
            "AWS US East 1 → botón «Connect» → copia la «connection string» completa (postgresql://…)."
        )).pack(fill="x", padx=16)
        boton_enlace("Abrir Neon", URL_NEON)
        self.neon = campo("Cadena de conexión")
        tk.Label(self.raiz, font=("Segoe UI", 9), fg="#52514e", anchor="w",
                 text="Si la app ya está publicada, déjalo vacío para conservar la base actual.").pack(fill="x", padx=16)

        seccion("3. Tu acceso a la app")
        self.contrasena = campo(f"Contraseña (mín. {LARGO_MINIMO})")
        self.contrasena2 = campo("Repite la contraseña")
        tk.Label(self.raiz, font=("Segoe UI", 9), fg="#52514e", anchor="w",
                 text="Déjalas vacías para conservar tu contraseña actual (solo se cambia el 2FA).").pack(fill="x", padx=16)
        tk.Button(self.raiz, text="Generar código QR para Google Authenticator", command=self.generar_qr).pack(
            anchor="w", padx=16, pady=6
        )
        self.lienzo = tk.Canvas(self.raiz, width=230, height=230, highlightthickness=0, bg="white")
        self.lienzo.pack()
        self.texto_secreto = tk.Label(self.raiz, text="", font=("Consolas", 9))
        self.texto_secreto.pack()
        self.codigo = campo("Código de 6 dígitos", oculto=False)

        seccion("4. Opcional: lectura de recibos con IA y recordatorios por Telegram")
        tk.Label(self.raiz, font=("Segoe UI", 9), fg="#52514e", anchor="w", justify="left", text=(
            "Clave de Anthropic: console.anthropic.com → API Keys. Bot de Telegram: escríbele a @BotFather → /newbot.\n"
            "Déjalos vacíos para no cambiarlos. El código QR del paso 3 también es opcional si ya tienes 2FA."
        )).pack(fill="x", padx=16)
        self.anthropic = campo("ANTHROPIC_API_KEY")
        self.telegram_token = campo("Token del bot (Telegram)")
        self.telegram_chat = campo("Tu chat ID (Telegram)", oculto=False)

        self.borrar_token = tk.BooleanVar(value=True)
        tk.Checkbutton(self.raiz, text="Borrar el token de Vercel al terminar (recomendado)",
                       variable=self.borrar_token, font=fuente).pack(pady=(10, 0))
        self.boton = tk.Button(self.raiz, text="Configurar y publicar", font=negrita, command=self.empezar)
        self.boton.pack(pady=12)
        self.estado = tk.Label(self.raiz, text="", wraplength=600, justify="left", font=("Segoe UI", 9))
        self.estado.pack(fill="x", padx=16)
        self.boton_abrir = tk.Button(self.raiz, text="Abrir la app", state="disabled")
        self.boton_abrir.pack(pady=6)

    # --- 2FA ---------------------------------------------------------------

    def generar_qr(self) -> None:
        import qrcode

        self.secreto_totp = pyotp.random_base32()
        uri = pyotp.TOTP(self.secreto_totp).provisioning_uri(
            name="mi-centro-de-control", issuer_name="Mi Centro de Control"
        )
        qr = qrcode.QRCode(border=2)
        qr.add_data(uri)
        qr.make(fit=True)
        matriz = qr.get_matrix()
        lado = 230 // len(matriz)
        self.lienzo.delete("all")
        for y, fila in enumerate(matriz):
            for x, negro in enumerate(fila):
                if negro:
                    self.lienzo.create_rectangle(x * lado, y * lado, (x + 1) * lado, (y + 1) * lado, fill="black", width=0)
        grupos = " ".join(self.secreto_totp[i : i + 4] for i in range(0, len(self.secreto_totp), 4))
        self.texto_secreto.config(text=f"Si no puedes escanear, clave manual: {grupos}")
        self.decir("Escanea el QR con Google Authenticator (+ → Escanear un código QR) y escribe el código que aparece.")

    # --- Interfaz ----------------------------------------------------------

    def decir(self, mensaje: str, error: bool = False) -> None:
        registrar(("ERROR: " if error else "") + mensaje)
        self.raiz.after(0, lambda: self.estado.config(text=mensaje, fg="#C62828" if error else "#1F3A5F"))

    def terminar(self, ok: bool) -> None:
        self.raiz.after(0, lambda: self.boton.config(state="normal", text="Cerrar" if ok else "Reintentar",
                                                     command=self.raiz.destroy if ok else self.empezar))

    def empezar(self) -> None:
        token = self.token.get().strip()
        neon = self.neon.get().strip()
        contrasena = self.contrasena.get()
        if not token or " " in token:
            return self.decir("Pega el token de Vercel (paso 1).", True)
        if neon:
            try:
                urls_neon(neon)
            except ValueError as exc:
                return self.decir(str(exc) + " (paso 2).", True)
        if contrasena or self.contrasena2.get():
            if len(contrasena) < LARGO_MINIMO:
                return self.decir(f"La contraseña debe tener al menos {LARGO_MINIMO} caracteres.", True)
            if contrasena != self.contrasena2.get():
                return self.decir("Las contraseñas no coinciden.", True)
        if self.secreto_totp and not pyotp.TOTP(self.secreto_totp).verify(self.codigo.get().replace(" ", ""), valid_window=1):
            return self.decir("El código de Authenticator no coincide. Revisa la hora del celular.", True)
        self.boton.config(state="disabled")
        threading.Thread(target=self.trabajar, args=(token, neon, contrasena), daemon=True).start()

    # --- Trabajo -----------------------------------------------------------

    def trabajar(self, token: str, neon: str, contrasena: str) -> None:
        try:
            self.publicar(token, neon, contrasena)
        except ErrorVercel as exc:
            self.decir(f"Vercel respondió con un error ({exc}).", True)
            self.terminar(False)
        except Exception as exc:  # nunca se muestran los secretos
            self.decir(f"Error inesperado: {type(exc).__name__}: {exc}", True)
            self.terminar(False)

    def publicar(self, token: str, neon: str, contrasena: str) -> None:
        self.decir("1/5 Verificando el token de Vercel…")
        # Las cuentas nuevas de Vercel trabajan dentro de un "equipo" (aunque sea Hobby): las llamadas
        # deben llevar su teamId. Un token con alcance de equipo no puede leer /v2/user.
        cuenta, equipo, token_ok = "?", None, False
        registrar(f"Token: {len(token)} caracteres")
        pruebas = []
        try:
            usuario = vercel("GET", "/v2/user", token).get("user", {})
            cuenta = usuario.get("username") or cuenta
            equipo = usuario.get("defaultTeamId")
            token_ok = True
        except ErrorVercel as exc:
            pruebas.append(f"/v2/user → {exc}")
        if not equipo:
            try:
                equipos = vercel("GET", "/v2/teams", token).get("teams", [])
                token_ok = True
                if equipos:
                    equipo = equipos[0]["id"]
                    cuenta = equipos[0].get("slug") or cuenta
            except ErrorVercel as exc:
                pruebas.append(f"/v2/teams → {exc}")
        if not equipo:
            # Último recurso: un token de equipo sí puede listar proyectos; su accountId es el equipo
            try:
                proyectos = vercel("GET", "/v10/projects", token, query={"limit": "20"}).get("projects", [])
                token_ok = True
                cuentas = {p.get("accountId") for p in proyectos if str(p.get("accountId", "")).startswith("team_")}
                if len(cuentas) == 1:
                    equipo = cuentas.pop()
            except ErrorVercel as exc:
                pruebas.append(f"/v10/projects → {exc}")
        for prueba in pruebas:
            registrar("Diagnóstico: " + prueba)
        if not token_ok:
            self.decir("Vercel rechazó el token (" + "; ".join(pruebas) + "). Crea uno nuevo en la página de "
                       "tokens, cópialo con el botón de copiar y pégalo en el paso 1.", True)
            return self.terminar(False)
        q = {"teamId": equipo} if equipo else {}
        registrar(f"Cuenta de Vercel: {cuenta} (equipo: {'sí' if equipo else 'no'})")

        self.decir(f"2/5 Preparando el proyecto en la cuenta «{cuenta}»…")
        try:
            proyecto = vercel("GET", f"/v9/projects/{PROYECTO}", token, query=q)
            registrar("Proyecto existente reutilizado")
        except ErrorVercel as exc:
            if exc.estado != 404:
                raise
            cuerpo = {"name": PROYECTO, "framework": "fastapi",
                      "gitRepository": {"type": "github", "repo": f"{DUENO_GITHUB}/{PROYECTO}"}}
            try:
                proyecto = vercel("POST", "/v11/projects", token, cuerpo, q)
            except ErrorVercel as exc2:
                texto = json.dumps(exc2.cuerpo).lower()
                if "framework" in texto:
                    cuerpo.pop("framework")
                    proyecto = vercel("POST", "/v11/projects", token, cuerpo, q)
                elif "github" in texto or "install" in texto or "repository" in texto:
                    webbrowser.open(URL_APP_GITHUB)
                    self.decir("Vercel no tiene acceso a tu repo de GitHub. Se abrió GitHub: instala la app «Vercel» "
                               f"y dale acceso a {PROYECTO}. Luego pulsa Reintentar.", True)
                    return self.terminar(False)
                else:
                    raise
            registrar("Proyecto creado")
        id_proyecto = proyecto["id"]

        self.decir("3/5 Guardando las variables de entorno (cifradas)…")
        existentes = {e.get("key") for e in vercel("GET", f"/v9/projects/{id_proyecto}/env", token, query=q).get("envs", [])}
        if not neon and "DATABASE_URL" not in existentes:
            self.decir("Este proyecto aún no tiene base de datos: pega la cadena de Neon (paso 2).", True)
            return self.terminar(False)
        if not contrasena and "APP_PASSWORD_HASH" not in existentes:
            self.decir("Este proyecto aún no tiene contraseña: escríbela en el paso 3.", True)
            return self.terminar(False)
        if not self.secreto_totp and "TOTP_SECRET" not in existentes:
            self.decir("Este proyecto aún no tiene 2FA: genera el código QR y escanéalo (paso 3).", True)
            return self.terminar(False)
        variables = {"ENTORNO": "produccion"}
        if neon:  # vacío = se conserva la base actual
            variables["DATABASE_URL"], variables["DATABASE_URL_MIGRACIONES"] = urls_neon(neon)
        if contrasena:  # vacío = se conserva la contraseña actual
            variables["APP_PASSWORD_HASH"] = PasswordHasher().hash(contrasena)
        if self.secreto_totp:  # vacío = se conserva el 2FA actual
            variables["TOTP_SECRET"] = self.secreto_totp
        if contrasena or self.secreto_totp or "SECRET_KEY" not in existentes:
            variables["SECRET_KEY"] = secrets.token_urlsafe(48)  # cierra las sesiones abiertas
        if "CRON_SECRET" not in existentes:
            variables["CRON_SECRET"] = secrets.token_urlsafe(32)
        for clave, campo_tk in (("ANTHROPIC_API_KEY", self.anthropic), ("TELEGRAM_BOT_TOKEN", self.telegram_token),
                                ("TELEGRAM_CHAT_ID", self.telegram_chat)):
            valor = campo_tk.get().strip()
            if valor:
                variables[clave] = valor
        cuerpo_env = [{"key": k, "value": v, "type": "encrypted", "target": ["production"]} for k, v in variables.items()]
        vercel("POST", f"/v10/projects/{id_proyecto}/env", token, cuerpo_env, {**q, "upsert": "true"})
        registrar("Variables guardadas: " + ", ".join(variables))

        self.decir("4/5 Lanzando el despliegue de producción (tarda 1-3 minutos)…")
        despliegue = vercel("POST", "/v13/deployments", token, {
            "name": PROYECTO,
            "project": id_proyecto,
            "target": "production",
            "gitSource": {"type": "github", "org": DUENO_GITHUB, "repo": PROYECTO, "ref": "main"},
        }, {**q, "forceNew": "1", "skipAutoDetectionConfirmation": "1"})
        id_despliegue = despliegue["id"]
        registrar(f"Despliegue {id_despliegue} creado")

        estado = despliegue.get("readyState", "QUEUED")
        inicio = time.time()
        while estado not in ("READY", "ERROR", "CANCELED") and time.time() - inicio < 600:
            time.sleep(6)
            despliegue = vercel("GET", f"/v13/deployments/{id_despliegue}", token, query=q)
            estado = despliegue.get("readyState", estado)
            self.decir(f"4/5 Desplegando… estado: {estado}")
        if estado != "READY":
            panel = f"https://vercel.com/{cuenta}/{PROYECTO}"
            self.decir(f"El despliegue terminó en {estado}. Revisa el registro de compilación en {panel}", True)
            registrar(f"Despliegue terminó en {estado}")
            return self.terminar(False)

        self.decir("5/5 Buscando la dirección pública…")
        dominios = vercel("GET", f"/v9/projects/{id_proyecto}/domains", token, query=q).get("domains", [])
        nombres = sorted((d["name"] for d in dominios if d.get("name", "").endswith(".vercel.app")), key=len)
        url = f"https://{nombres[0]}" if nombres else f"https://{despliegue.get('url')}"
        ARCHIVO_URL.write_text(url + "\n", encoding="utf-8")
        registrar(f"Listo: {url}")
        nota_token = "Ya puedes borrar el token en Vercel y cerrar esta ventana."
        if self.borrar_token.get():
            try:
                vercel("DELETE", "/v3/user/tokens/current", token)
                registrar("Token de Vercel borrado")
                nota_token = "El token de Vercel ya fue borrado. Puedes cerrar esta ventana."
            except ErrorVercel as exc:
                registrar(f"No se pudo borrar el token: {exc}")
                nota_token = "No pude borrar el token de Vercel: bórralo en Account Settings → Tokens."
        self.decir(f"LISTO: {url}\nEntra con tu contraseña y el código NUEVO de Authenticator. {nota_token}")
        self.raiz.after(0, lambda: self.boton_abrir.config(state="normal", command=lambda: webbrowser.open(url)))
        self.terminar(True)


if __name__ == "__main__":
    REGISTRO.write_text("", encoding="utf-8")
    registrar("Ventana abierta")
    Ventana().raiz.mainloop()
    registrar("Ventana cerrada")
