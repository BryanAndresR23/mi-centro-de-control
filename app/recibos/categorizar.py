"""Sugerencia de categoría por comercio: primero tus reglas aprendidas, luego la sugerencia de la IA."""

from __future__ import annotations

import re
import unicodedata

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Categoria, ReglaCategoria


def patron_de(texto: str | None) -> str:
    """'SUPERMAXI EL BOSQUE #123' -> 'supermaxi el bosque'. Sin tildes, números ni signos."""
    texto = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode().lower()
    texto = re.sub(r"[^a-z ]+", " ", texto)
    return " ".join(p for p in texto.split() if len(p) > 1)[:120]


# Comercios frecuentes en Ecuador → categoría base (se usa si no hay una regla aprendida)
PALABRAS_CLAVE = {
    "Supermercado": ("supermaxi", "megamaxi", "comisariato", "tia ", "almacenes tia", "santa maria", "gran aki", "aki ",
                     "coral", "favorita", "tuti", "despensa"),
    "Comida fuera": ("kfc", "mcdonald", "burger", "pizza", "restaurante", "menestras", "cevicheria", "pollos", "dominos",
                     "tropi", "sushi", "parrillada", "comida"),
    "Café y snacks": ("sweet coffee", "juan valdez", "cafe", "starbucks", "panaderia", "dulcería", "heladeria"),
    "Transporte": ("uber", "cabify", "indrive", "didi", "primax", "petroecuador", "gasolinera", "terpel", "metro de quito", "taxi"),
    "Salud": ("fybeca", "pharmacys", "sana sana", "cruz azul", "medicity", "farmacia", "clinica", "hospital", "laboratorio"),
    "Suscripciones": ("netflix", "spotify", "disney", "hbo", "max ", "youtube", "apple com", "google", "prime video", "chatgpt"),
    "Internet y celular": ("claro", "movistar", "cnt", "netlife", "tvcable", "xtrim", "puntonet"),
    "Servicios básicos": ("empresa electrica", "eeq", "epmaps", "agua potable", "electrica"),
    "Vestimenta": ("de prati", "etafashion", "marathon", "rm ", "zara", "pinto", "bunky"),
    "Entretenimiento": ("supercines", "cinemark", "multicines", "teatro", "steam", "playstation"),
    "Gimnasio y deporte": ("gym", "gimnasio", "smartfit", "crossfit"),
    "Educación": ("universidad", "colegio", "escuela", "curso", "udemy", "coursera", "platzi", "libreria"),
    "Viajes": ("hotel", "hostal", "airbnb", "booking", "latam", "avianca", "tame", "aeropuerto"),
}


def sugerir_categoria(bd: Session, comercio: str | None, sugerida: str | None, tipo: str = "gasto") -> int | None:
    patron = patron_de(comercio)
    if patron:
        reglas = bd.scalars(select(ReglaCategoria).order_by(ReglaCategoria.veces.desc())).all()
        for regla in reglas:
            if regla.patron and (regla.patron in patron or patron in regla.patron):
                categoria = bd.get(Categoria, regla.categoria_id)
                if categoria and categoria.tipo == tipo and categoria.activa:
                    return categoria.id
        if tipo == "gasto" and not sugerida:
            texto = f" {patron} "
            for nombre, claves in PALABRAS_CLAVE.items():
                if any(f" {clave.strip()} " in texto or clave.strip() in patron.split() or
                       (len(clave.strip()) > 5 and clave.strip() in patron) for clave in claves):
                    sugerida = nombre
                    break
    if sugerida:
        categoria = bd.scalar(select(Categoria).where(Categoria.nombre == sugerida, Categoria.tipo == tipo))
        if categoria:
            return categoria.id
    respaldo = "Otros gastos" if tipo == "gasto" else "Otros ingresos"
    return bd.scalar(select(Categoria.id).where(Categoria.nombre == respaldo))


def aprender(bd: Session, comercio: str | None, categoria_id: int) -> None:
    """Guarda (o refuerza) la regla comercio → categoría tras una confirmación."""
    patron = patron_de(comercio)
    if len(patron) < 3:
        return
    regla = bd.scalar(select(ReglaCategoria).where(ReglaCategoria.patron == patron))
    if regla is None:
        bd.add(ReglaCategoria(patron=patron, categoria_id=categoria_id, veces=1))
    elif regla.categoria_id == categoria_id:
        regla.veces += 1
    else:
        regla.categoria_id, regla.veces = categoria_id, 1
