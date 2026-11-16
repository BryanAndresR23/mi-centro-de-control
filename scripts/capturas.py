"""Genera las capturas del README con los DATOS DE EJEMPLO de la base local.

Requisitos: servidor local con la demo (scripts/datos_demo.py + credenciales --demo) en http://localhost:8000
y Microsoft Edge instalado. Usa Edge sin ventana, controlado por el protocolo DevTools.

Uso: python scripts/capturas.py
"""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from websockets.sync.client import connect

sys.path.insert(0, str(Path(__file__).resolve().parent))
from crear_credenciales import CONTRASENA_DEMO  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
DESTINO = RAIZ / "docs" / "capturas"
BASE = "http://localhost:8000"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PUERTO = 9229
MES = "2026-10"  # un mes completo de la demo

MOVIL = {"width": 390, "height": 844, "deviceScaleFactor": 2, "mobile": True}
ESCRITORIO = {"width": 1280, "height": 900, "deviceScaleFactor": 1, "mobile": False}

CAPTURAS = [
    # (archivo, ruta, pantalla, tema, página completa)
    ("01-dashboard-escritorio.png", f"/?mes={MES}", ESCRITORIO, "light", True),
    ("02-dashboard-movil.png", f"/?mes={MES}", MOVIL, "light", False),
    ("03-movimientos-movil.png", f"/movimientos?mes={MES}", MOVIL, "light", False),
    ("04-nuevo-movimiento-movil.png", "/movimientos/nuevo", MOVIL, "light", False),
    ("05-cuentas-movil.png", "/cuentas", MOVIL, "light", False),
    ("06-dashboard-oscuro-movil.png", f"/?mes={MES}", MOVIL, "dark", False),
    ("07-login-movil.png", "/login", MOVIL, "light", False),
]


class Navegador:
    def __init__(self, url_ws: str):
        self.ws = connect(url_ws, max_size=100_000_000, legacy=True)
        self.siguiente = 0

    def llamar(self, metodo: str, **params):
        self.siguiente += 1
        mi_id = self.siguiente
        self.ws.send(json.dumps({"id": mi_id, "method": metodo, "params": params}))
        while True:
            mensaje = json.loads(self.ws.recv())
            if mensaje.get("id") == mi_id:
                if "error" in mensaje:
                    raise RuntimeError(f"{metodo}: {mensaje['error']}")
                return mensaje.get("result", {})

    def evaluar(self, expresion: str):
        return self.llamar("Runtime.evaluate", expression=expresion, returnByValue=True, awaitPromise=True)[
            "result"
        ].get("value")

    def ir(self, url: str, espera: float = 0.6) -> None:
        self.llamar("Page.navigate", url=url)
        for _ in range(100):
            time.sleep(0.15)
            if self.evaluar("document.readyState") == "complete":
                break
        time.sleep(espera)


def main() -> None:
    if not BASE.startswith("http://localhost"):
        sys.exit("Solo se capturan datos de ejemplo del servidor local.")
    DESTINO.mkdir(parents=True, exist_ok=True)
    perfil = tempfile.mkdtemp(prefix="capturas-edge-")
    edge = subprocess.Popen([
        EDGE, "--headless=new", f"--remote-debugging-port={PUERTO}", f"--user-data-dir={perfil}",
        "--no-first-run", "--disable-extensions", "--hide-scrollbars", "about:blank",
    ])
    try:
        for _ in range(100):
            try:
                objetivos = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PUERTO}/json/list"))
                pagina = next(t for t in objetivos if t.get("type") == "page")
                break
            except Exception:
                time.sleep(0.2)
        else:
            sys.exit("Edge no abrió el puerto de depuración.")
        nav = Navegador(pagina["webSocketDebuggerUrl"])
        nav.llamar("Page.enable")
        nav.llamar("Runtime.enable")

        # Inicio de sesión con la contraseña de la demo local (documentada en el README)
        nav.llamar("Emulation.setDeviceMetricsOverride", **ESCRITORIO)
        nav.ir(BASE + "/login")
        nav.evaluar(
            "document.querySelector('#password').value = " + json.dumps(CONTRASENA_DEMO) + ";"
            "document.querySelector('form').submit(); true"
        )
        time.sleep(1.5)
        if nav.evaluar("location.pathname") == "/login":
            # La captura del login se hace al final, ya con la sesión cerrada
            sys.exit("No se pudo entrar a la demo local: ¿se ejecutó crear_credenciales.py --demo?")

        for archivo, ruta, pantalla, tema, completa in CAPTURAS:
            if ruta == "/login":
                nav.evaluar("fetch('/logout', {method: 'POST', headers: {'X-CSRF-Token': "
                            "document.querySelector('meta[name=csrf-token]').content}}).then(() => true)")
            nav.llamar("Emulation.setDeviceMetricsOverride", **pantalla)
            nav.llamar("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": tema}])
            nav.ir(BASE + ruta, espera=1.2)
            parametros = {"format": "png"}
            if completa:
                alto = nav.evaluar("document.documentElement.scrollHeight")
                parametros.update(captureBeyondViewport=True,
                                  clip={"x": 0, "y": 0, "width": pantalla["width"], "height": alto, "scale": 1})
            imagen = nav.llamar("Page.captureScreenshot", **parametros)["data"]
            (DESTINO / archivo).write_bytes(base64.b64decode(imagen))
            print("✓", archivo)
    finally:
        edge.terminate()
        try:
            edge.wait(timeout=10)
        except subprocess.TimeoutExpired:
            edge.kill()
        shutil.rmtree(perfil, ignore_errors=True)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
