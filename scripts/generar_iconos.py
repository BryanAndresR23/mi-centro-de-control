"""Genera los íconos PNG de la app (los mismos del icono.svg) sin dependencias externas.

Uso: python scripts/generar_iconos.py
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

DESTINO = Path(__file__).resolve().parent.parent / "app" / "static" / "iconos"
FONDO = (0x1C, 0x5C, 0xAB)
BLANCO = (0xFF, 0xFF, 0xFF)
# Mismas barras del SVG, en una cuadrícula de 64 unidades: (x, y, ancho, alto)
BARRAS = [(15, 34, 8, 16), (28, 25, 8, 25), (41, 14, 8, 36)]


def _dentro_redondeado(px: float, py: float, x: float, y: float, w: float, h: float, r: float) -> bool:
    if not (x <= px <= x + w and y <= py <= y + h):
        return False
    cx = min(max(px, x + r), x + w - r)
    cy = min(max(py, y + r), y + h - r)
    return (px - cx) ** 2 + (py - cy) ** 2 <= r * r


def _png(ancho: int, filas: list[bytes]) -> bytes:
    def bloque(tipo: bytes, datos: bytes) -> bytes:
        return struct.pack(">I", len(datos)) + tipo + datos + struct.pack(">I", zlib.crc32(tipo + datos) & 0xFFFFFFFF)

    crudo = b"".join(b"\x00" + fila for fila in filas)
    cabecera = struct.pack(">IIBBBBB", ancho, len(filas), 8, 6, 0, 0, 0)  # RGBA 8 bits
    return b"\x89PNG\r\n\x1a\n" + bloque(b"IHDR", cabecera) + bloque(b"IDAT", zlib.compress(crudo, 9)) + bloque(b"IEND", b"")


def generar(tamano: int, esquinas: bool = True, muestras: int = 4) -> bytes:
    """Antialiasing por supermuestreo: `muestras`² subpíxeles por píxel."""
    escala = 64 / tamano
    filas = []
    for fy in range(tamano):
        fila = bytearray()
        for fx in range(tamano):
            fondo = blanco = 0
            for sy in range(muestras):
                for sx in range(muestras):
                    px = (fx + (sx + 0.5) / muestras) * escala
                    py = (fy + (sy + 0.5) / muestras) * escala
                    if esquinas and not _dentro_redondeado(px, py, 0, 0, 64, 64, 14):
                        continue
                    if any(_dentro_redondeado(px, py, *b, 2) for b in BARRAS):
                        blanco += 1
                    else:
                        fondo += 1
            total = muestras * muestras
            cubierto = fondo + blanco
            if cubierto == 0:
                fila += bytes((0, 0, 0, 0))
                continue
            color = [round((FONDO[i] * fondo + BLANCO[i] * blanco) / cubierto) for i in range(3)]
            fila += bytes((*color, round(255 * cubierto / total)))
        filas.append(bytes(fila))
    return _png(tamano, filas)


if __name__ == "__main__":
    DESTINO.mkdir(parents=True, exist_ok=True)
    # Android recorta los íconos "maskable": sin esquinas transparentes, el fondo llena todo
    (DESTINO / "icono-192.png").write_bytes(generar(192))
    (DESTINO / "icono-512.png").write_bytes(generar(512, esquinas=False, muestras=2))
    (DESTINO / "apple-touch-icon.png").write_bytes(generar(180, esquinas=False))
    print(f"Íconos generados en {DESTINO}")
