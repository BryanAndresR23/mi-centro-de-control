"""Cliente de Claude compartido (lectura de recibos y asesor).

Se activa con la variable de entorno ANTHROPIC_API_KEY. El modelo se cambia con MODELO_IA
(por defecto claude-opus-5-5; claude-haiku-5-5 cuesta mucho menos y sirve para recibos nítidos).
"""

from __future__ import annotations

import json
import os

MODELO_DEFECTO = "claude-opus-5-5"
# Modelos que aceptan el respaldo automático del servidor si el modelo rechaza una solicitud
MODELOS_CON_RESPALDO = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-2", "claude-fable-5-1", "claude-sonnet-5-5"}


class IANoConfigurada(Exception):
    pass


class ErrorIA(Exception):
    pass


def configurada() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY", "").strip())


def modelo() -> str:
    return os.getenv("MODELO_IA", "").strip() or MODELO_DEFECTO


def pedir_json(system: str, contenido: list | str, esquema: dict, max_tokens: int = 4000, esfuerzo: str = "low"):
    """Envía la solicitud con salida JSON obligatoria (structured outputs). Devuelve (datos, modelo_usado)."""
    if not configurada():
        raise IANoConfigurada("Falta la variable de entorno ANTHROPIC_API_KEY.")
    import anthropic

    cliente = anthropic.Anthropic(timeout=55, max_retries=1)
    m = modelo()
    parametros = {
        "model": m,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": contenido}],
        "output_config": {"format": {"type": "json_schema", "schema": esquema}, "effort": esfuerzo},
    }
    try:
        if m in MODELOS_CON_RESPALDO:
            respuesta = cliente.beta.messages.create(
                **parametros, betas=["server-side-fallback-2026-07-01"], fallbacks="default"
            )
        else:
            respuesta = cliente.messages.create(**parametros)
    except anthropic.AuthenticationError as exc:
        raise ErrorIA("La clave ANTHROPIC_API_KEY no es válida.") from exc
    except anthropic.RateLimitError as exc:
        raise ErrorIA("Se alcanzó el límite de uso de la API. Intenta en un minuto.") from exc
    except anthropic.APIConnectionError as exc:
        raise ErrorIA("No se pudo conectar con la API de Claude.") from exc
    except anthropic.APIStatusError as exc:
        raise ErrorIA(f"La API de Claude respondió con un error ({exc.status_code}).") from exc

    if respuesta.stop_reason == "refusal":
        raise ErrorIA("El modelo no procesó esta solicitud.")
    if respuesta.stop_reason == "max_tokens":
        raise ErrorIA("La respuesta quedó incompleta.")
    texto = "".join(b.text for b in respuesta.content if getattr(b, "type", "") == "text")
    try:
        return json.loads(texto), respuesta.model
    except json.JSONDecodeError as exc:
        raise ErrorIA("La respuesta no fue un JSON válido.") from exc
