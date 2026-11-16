"""Recordatorios por Telegram (bot propio). Variables: TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


def _token() -> str:
    return os.getenv("TELEGRAM_BOT_TOKEN", "").strip()


def configurado() -> bool:
    return bool(_token() and os.getenv("TELEGRAM_CHAT_ID", "").strip())


def _llamar(metodo: str, datos: dict | None = None) -> dict | None:
    pedido = urllib.request.Request(
        f"https://api.telegram.org/bot{_token()}/{metodo}",
        data=json.dumps(datos or {}).encode(), method="POST", headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(pedido, timeout=10) as respuesta:
            return json.loads(respuesta.read())
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None


def enviar(texto_html: str) -> bool:
    if not configurado():
        return False
    respuesta = _llamar("sendMessage", {"chat_id": os.getenv("TELEGRAM_CHAT_ID", "").strip(), "text": texto_html[:4000],
                                        "parse_mode": "HTML", "disable_web_page_preview": True})
    return bool(respuesta and respuesta.get("ok"))


def chats_recientes() -> list[tuple[str, str]]:
    """Chats que le escribieron al bot (para averiguar tu TELEGRAM_CHAT_ID)."""
    if not _token():
        return []
    respuesta = _llamar("getUpdates") or {}
    chats = {}
    for actualizacion in respuesta.get("result", []):
        chat = (actualizacion.get("message") or {}).get("chat") or {}
        if "id" in chat:
            chats[str(chat["id"])] = chat.get("first_name") or chat.get("title") or chat.get("username") or ""
    return list(chats.items())
