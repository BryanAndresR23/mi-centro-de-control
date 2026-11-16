"""Sube el código a un repositorio PRIVADO de GitHub sin tener Git instalado (usa dulwich).

Uso:  python scripts/subir_github.py ["mensaje del commit"]
      python scripts/subir_github.py --lista        (solo muestra qué se subiría)

1. Hace un commit con los archivos del proyecto (nunca .env, la base local ni el entorno virtual).
2. Abre una ventana para pegar tu token de GitHub (oculto; no se guarda ni se muestra).
3. Crea el repositorio privado si no existe y sube la rama main.

Token: GitHub → Settings → Developer settings → Tokens (classic) → permiso «repo».
"""

from __future__ import annotations

import fnmatch
import json
import re
import sys
import threading
import tkinter as tk
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from dulwich import porcelain
from dulwich.repo import Repo

DUENO = "BryanAndresR23"
REPO = "mi-centro-de-control"
AUTOR = b"Bryan Andres Romo Arias <292515987+BryanAndresR23@users.noreply.github.com>"
RAIZ = Path(__file__).resolve().parent.parent
URL_TOKEN = "https://github.com/settings/tokens/new?scopes=repo&description=Mi%20Centro%20de%20Control"

CARPETAS_EXCLUIDAS = {".git", ".venv", "__pycache__", ".pytest_cache", ".vercel", ".claude", "respaldos", "fixtures_privados"}
ARCHIVOS_EXCLUIDOS = [".env", ".env.*", "*.db", "*.db-journal", "credenciales_qr.svg", "*.pyc"]
PERMITIDOS = {".env.example"}
# Si algún archivo contiene esto, no se sube nada
PATRONES_SECRETOS = [
    re.compile(rb"APP_PASSWORD_HASH\s*=\s*'?\$argon2"),
    re.compile(rb"TOTP_SECRET\s*=\s*'?[A-Z2-7]{16,}"),
    re.compile(rb"postgres(ql)?://[^\s'\"]+:[^\s'\"@]+@"),
    re.compile(rb"\b(ghp_|github_pat_)[A-Za-z0-9_]{20,}"),
    re.compile(rb"\bsk-ant-[A-Za-z0-9_-]{20,}"),
]


def archivos_del_proyecto() -> list[Path]:
    archivos = []
    for ruta in RAIZ.rglob("*"):
        relativa = ruta.relative_to(RAIZ)
        if not ruta.is_file() or CARPETAS_EXCLUIDAS & set(relativa.parts):
            continue
        if ruta.name not in PERMITIDOS and any(fnmatch.fnmatch(ruta.name, p) for p in ARCHIVOS_EXCLUIDOS):
            continue
        archivos.append(ruta)
    return sorted(archivos)


def revisar_secretos(archivos: list[Path]) -> list[str]:
    problemas = []
    for ruta in archivos:
        contenido = ruta.read_bytes()
        if any(p.search(contenido) for p in PATRONES_SECRETOS):
            problemas.append(str(ruta.relative_to(RAIZ)))
    return problemas


def commit(mensaje: str) -> None:
    if not (RAIZ / ".git").exists():
        porcelain.init(str(RAIZ))
        Repo(str(RAIZ)).refs.set_symbolic_ref(b"HEAD", b"refs/heads/main")
    archivos = archivos_del_proyecto()
    problemas = revisar_secretos(archivos)
    if problemas:
        sys.exit("ALTO: estos archivos parecen tener secretos y no se subirán:\n  " + "\n  ".join(problemas))
    porcelain.add(str(RAIZ), paths=[str(p) for p in archivos])
    # Archivos que ya no existen: se quitan del índice
    indice = Repo(str(RAIZ)).open_index()
    actuales = {p.relative_to(RAIZ).as_posix().encode() for p in archivos}
    borrados = [RAIZ / ruta.decode() for ruta in indice if ruta not in actuales]
    if borrados:
        porcelain.remove(str(RAIZ), paths=[str(p) for p in borrados], cached=True)
    estado = porcelain.status(str(RAIZ))
    if any(estado.staged.values()):
        sha = porcelain.commit(str(RAIZ), message=mensaje.encode(), author=AUTOR, committer=AUTOR)
        print(f"Commit {sha.decode()[:10]} con {len(archivos)} archivos.")
    else:
        print("Sin cambios nuevos desde el último commit.")


def api(metodo: str, ruta: str, token: str, cuerpo: dict | None = None):
    pedido = urllib.request.Request(
        "https://api.github.com" + ruta,
        method=metodo,
        data=json.dumps(cuerpo).encode() if cuerpo is not None else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "subir-codigo",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(pedido, timeout=60) as respuesta:
            return respuesta.status, json.loads(respuesta.read() or b"{}"), respuesta.headers
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}"), error.headers


def publicar(token: str, decir) -> None:
    estado, yo, cabeceras = api("GET", "/user", token)
    if estado != 200:
        return decir(f"El token no es válido o expiró (HTTP {estado}).", True)
    if yo.get("login", "").lower() != DUENO.lower():
        return decir(f"El token es de «{yo.get('login')}», no de {DUENO}.", True)
    permisos = {s.strip() for s in (cabeceras.get("X-OAuth-Scopes") or "").split(",") if s.strip()}
    if cabeceras.get("X-OAuth-Scopes") is not None and "repo" not in permisos:
        return decir("Al token le falta el permiso «repo» (necesario para repositorios privados).", True)

    decir("1/3 Preparando el repositorio privado…")
    estado, _, _ = api("GET", f"/repos/{DUENO}/{REPO}", token)
    if estado == 404:
        estado, cuerpo, _ = api("POST", "/user/repos", token, {
            "name": REPO, "private": True, "has_wiki": False, "has_projects": False,
            "description": "Finanzas personales, pendientes y metas (app personal).",
        })
        if estado != 201:
            return decir(f"No se pudo crear el repositorio (HTTP {estado}): {cuerpo.get('message')}", True)

    decir("2/3 Subiendo el código…")
    porcelain.push(str(RAIZ), f"https://github.com/{DUENO}/{REPO}.git", "refs/heads/main",
                   username=DUENO, password=token)

    decir("3/3 Comprobando…")
    estado, info, _ = api("GET", f"/repos/{DUENO}/{REPO}", token)
    if estado != 200 or not info.get("private"):
        return decir(f"ATENCIÓN: el repositorio no figura como privado (HTTP {estado}). Revísalo en GitHub.", True)
    decir(f"LISTO: https://github.com/{DUENO}/{REPO} (privado).\nYa puedes cerrar esta ventana.", done=True)


def ventana_token() -> None:
    raiz = tk.Tk()
    raiz.title("Subir Mi Centro de Control a GitHub")
    raiz.attributes("-topmost", True)
    raiz.geometry("600x280")
    tk.Label(raiz, text=f"Se subirá el código al repositorio PRIVADO {DUENO}/{REPO}.",
             font=("Segoe UI", 10, "bold")).pack(pady=(14, 6))
    tk.Label(raiz, text="Pega tu token de GitHub (empieza por ghp_) y pulsa Subir:", font=("Segoe UI", 10)).pack()
    campo = tk.Entry(raiz, show="*", width=58, font=("Segoe UI", 10))
    campo.pack(pady=6)
    campo.focus_set()
    estado = tk.Label(raiz, text="", wraplength=560, font=("Segoe UI", 9))
    estado.pack(pady=8)
    fila = tk.Frame(raiz)
    fila.pack()
    boton = tk.Button(fila, text="Subir", width=14)
    boton.pack(side="left", padx=6)
    tk.Button(fila, text="Crear token en GitHub", width=20, command=lambda: webbrowser.open(URL_TOKEN)).pack(side="left", padx=6)

    def decir(mensaje: str, error: bool = False, done: bool = False) -> None:
        raiz.after(0, lambda: estado.config(text=mensaje, fg="#C62828" if error else "#1F3A5F"))
        if error or done:
            raiz.after(0, lambda: boton.config(state="normal", text="Cerrar" if done else "Reintentar",
                                               command=raiz.destroy if done else empezar))

    def trabajo(token: str) -> None:
        try:
            publicar(token, decir)
        except Exception as exc:  # nunca se muestra el token
            decir(f"Error: {type(exc).__name__}: {exc}", True)

    def empezar(*_):
        token = campo.get().strip()
        if not (token.startswith("ghp_") or token.startswith("github_pat_")):
            campo.delete(0, "end")
            return decir("Eso no parece un token de GitHub (debe empezar por ghp_ o github_pat_).", True)
        boton.config(state="disabled")
        decir("Verificando el token…")
        threading.Thread(target=trabajo, args=(token,), daemon=True).start()

    boton.config(command=empezar)
    campo.bind("<Return>", empezar)
    raiz.mainloop()


if __name__ == "__main__":
    banderas = {a for a in sys.argv[1:] if a.startswith("--")}
    textos = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--lista" in banderas:  # solo muestra qué se subiría, sin tocar nada
        archivos = archivos_del_proyecto()
        print("\n".join(p.relative_to(RAIZ).as_posix() for p in archivos))
        problemas = revisar_secretos(archivos)
        print(f"\n{len(archivos)} archivos. Secretos detectados: {problemas or 'ninguno'}")
        sys.exit(1 if problemas else 0)
    commit(textos[0] if textos else "Actualización de Mi Centro de Control")
    if "--solo-commit" not in banderas:
        ventana_token()
