"""Chequeos del proyecto que evitan sorpresas al desplegar."""

import tomllib

from app.config import RAIZ


def test_dependencias_iguales_en_pyproject_y_requirements():
    pyproject = tomllib.loads((RAIZ / "pyproject.toml").read_text(encoding="utf-8"))
    desde_pyproject = sorted(pyproject["project"]["dependencies"])
    lineas = (RAIZ / "requirements.txt").read_text(encoding="utf-8").splitlines()
    desde_requirements = sorted(l.strip() for l in lineas if l.strip() and not l.startswith("#"))
    assert desde_pyproject == desde_requirements


def test_vercel_apunta_a_la_app():
    pyproject = tomllib.loads((RAIZ / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["tool"]["vercel"]["entrypoint"] == "app.main:app"
    from app.main import app  # noqa: F401  (se puede importar)


def test_gitignore_protege_secretos_y_datos():
    ignorados = (RAIZ / ".gitignore").read_text(encoding="utf-8")
    for patron in (".env", "*.db", ".venv/", "credenciales_qr.svg"):
        assert patron in ignorados
