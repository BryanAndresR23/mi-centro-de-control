"""Fase 4: asesor financiero, revisión semanal y recordatorios diarios (cron de Vercel + Telegram)."""

from __future__ import annotations

import hmac
import os
from datetime import date, timedelta
from html import escape

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import ia, notificaciones
from ..asesor.informe import generar_financiero, generar_revision
from ..db import obtener_bd
from ..finanzas.montos import formato_usd
from ..finanzas.planificacion import estado_presupuestos
from ..finanzas.servicio import listar_categorias, movs_para_calculo, padre_de, presupuestos_datos
from ..models import AvisoEnviado, Beca, Informe, Postulacion, Recurrente, Tarea
from ..pendientes import logica
from ..seguridad import requiere_login, verificar_csrf
from ..tiempo import fecha_larga, hoy
from ..web import avisar, render

router = APIRouter(prefix="/asesor", dependencies=[Depends(requiere_login)])
cron = APIRouter(prefix="/cron")
TIPOS = {"semanal": "Análisis semanal", "mensual": "Análisis mensual", "revision": "Revisión semanal"}


@router.get("")
def pagina(request: Request, bd: Session = Depends(obtener_bd)):
    ultimos = {t: bd.scalar(select(Informe).where(Informe.tipo == t).order_by(Informe.id.desc())) for t in TIPOS}
    historial = list(bd.scalars(select(Informe).order_by(Informe.id.desc()).limit(12)))
    return render(request, "asesor/pagina.html", {
        "seccion": "mas", "ultimos": ultimos, "historial": historial, "tipos": TIPOS,
        "ia_activa": ia.configurada(), "modelo": ia.modelo(), "telegram": notificaciones.configurado(),
        "token_telegram": bool(os.getenv("TELEGRAM_BOT_TOKEN")), "cron": bool(os.getenv("CRON_SECRET")),
    })


@router.get("/informe/{iid}")
def ver_informe(request: Request, iid: int, bd: Session = Depends(obtener_bd)):
    informe = bd.get(Informe, iid)
    if informe is None:
        raise HTTPException(404, "Ese informe no existe.")
    return render(request, "asesor/informe.html", {"seccion": "mas", "informe": informe, "tipos": TIPOS})


@router.post("/generar/{tipo}", dependencies=[Depends(verificar_csrf)])
def generar(request: Request, tipo: str, bd: Session = Depends(obtener_bd)):
    if tipo not in TIPOS:
        raise HTTPException(404, "Tipo de informe desconocido.")
    informe = generar_revision(bd, hoy()) if tipo == "revision" else generar_financiero(bd, tipo, hoy())
    avisar(request, f"{TIPOS[tipo]} listo" + (" (redactado con IA)." if informe.modelo else " (sin IA: hechos calculados)."))
    return RedirectResponse(f"/asesor/informe/{informe.id}", status_code=303)


@router.post("/telegram/prueba", dependencies=[Depends(verificar_csrf)])
def prueba_telegram(request: Request):
    ok = notificaciones.enviar("✅ <b>Mi Centro de Control</b>: los recordatorios por Telegram funcionan.")
    avisar(request, "Mensaje de prueba enviado a Telegram." if ok else
           "No se pudo enviar: revisa TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID.", "ok" if ok else "error")
    return RedirectResponse("/asesor", status_code=303)


@router.get("/telegram/chats")
def chats_telegram(request: Request):
    return render(request, "asesor/chats.html", {"seccion": "mas", "chats": notificaciones.chats_recientes()})


# =============================================================================
# Cron diario (Vercel lo llama a las 12:00 UTC = 07:00 en Quito)
# =============================================================================


def _primera_vez(bd: Session, clave: str) -> bool:
    """Registra el aviso; False si ya se había enviado (así no se repite)."""
    if bd.scalar(select(AvisoEnviado.id).where(AvisoEnviado.clave == clave)):
        return False
    bd.add(AvisoEnviado(clave=clave))
    try:
        bd.flush()
    except IntegrityError:
        bd.rollback()
        return False
    return True


def resumen_diario(bd: Session, hoy_: date) -> tuple[str, int]:
    """Mensaje del día (HTML de Telegram) y cantidad de avisos incluidos."""
    lineas = [f"☀️ <b>{escape(fecha_larga(hoy_).capitalize())}</b>"]
    avisos = 0
    abiertas = list(bd.scalars(select(Tarea).where(Tarea.estado.in_(("pendiente", "en_curso")))))
    ordenadas = logica.ordenar_tareas([logica.TareaDato(t.id, t.fecha_limite, t.prioridad, t.estado) for t in abiertas], hoy_)
    por_id = {t.id: t for t in abiertas}
    urgentes = [por_id[t.id] for t in ordenadas if logica.urgencia(t.fecha_limite, hoy_) <= 1]
    if urgentes:
        lineas.append(f"\n📋 <b>Para hoy ({len(urgentes)})</b>")
        for t in urgentes[:8]:
            marca = "⛔" if t.fecha_limite and t.fecha_limite < hoy_ else "•"
            lineas.append(f"{marca} {escape(t.titulo)}")
        avisos += len(urgentes)

    pagos = [r for r in bd.scalars(select(Recurrente).where(Recurrente.activa.is_(True)))
             if (r.proxima_fecha - hoy_).days <= r.dias_aviso]
    if pagos:
        lineas.append("\n💳 <b>Pagos</b>")
        for r in sorted(pagos, key=lambda x: x.proxima_fecha):
            dias = (r.proxima_fecha - hoy_).days
            cuando = "vencido" if dias < 0 else "hoy" if dias == 0 else f"en {dias} día{'s' if dias != 1 else ''}"
            lineas.append(f"• {escape(r.nombre)}: {formato_usd(r.monto)} ({cuando})")
        avisos += len(pagos)

    nombres = {c.id: c.nombre for c in listar_categorias(bd)}
    for e in estado_presupuestos(presupuestos_datos(bd), movs_para_calculo(bd), hoy_.year, hoy_.month, padre_de(bd), hoy_):
        if e.nivel != "ok" and _primera_vez(bd, f"presupuesto:{e.presupuesto_id}:{hoy_:%Y-%m}:{e.nivel}"):
            icono = "⛔" if e.nivel == "excedido" else "⚠️"
            lineas.append(f"\n{icono} Presupuesto de {escape(nombres.get(e.categoria_id, ''))}: "
                          f"{formato_usd(e.gastado)} de {formato_usd(e.monto)} ({int(e.pct * 100)} %)")
            avisos += 1

    for b in bd.scalars(select(Beca).where(Beca.fecha_limite.is_not(None), Beca.estado.in_(("investigando", "preparando")))):
        dias = (b.fecha_limite - hoy_).days
        if dias in (30, 14, 7, 3, 1, 0):
            faltan = sum(1 for r in b.requisitos if not r.hecho)
            lineas.append(f"\n🎓 La beca <b>{escape(b.programa)}</b> cierra en {dias} días (faltan {faltan} requisitos).")
            avisos += 1
    for p in bd.scalars(select(Postulacion).where(Postulacion.fecha_proximo_paso.is_not(None),
                                                  Postulacion.fecha_proximo_paso.in_((hoy_, hoy_ + timedelta(days=1))))):
        cuando = "hoy" if p.fecha_proximo_paso == hoy_ else "mañana"
        lineas.append(f"\n📨 {escape(p.empresa)}: {escape(p.proximo_paso or 'próximo paso')} ({cuando})")
        avisos += 1
    return "\n".join(lineas), avisos


@cron.get("/diario")
def cron_diario(request: Request, bd: Session = Depends(obtener_bd)):
    secreto = os.getenv("CRON_SECRET", "")
    enviado = request.headers.get("authorization", "")
    if not secreto:
        return JSONResponse({"error": "CRON_SECRET no configurado"}, status_code=503)
    if not hmac.compare_digest(enviado, f"Bearer {secreto}"):
        return JSONResponse({"error": "no autorizado"}, status_code=401)
    hoy_ = hoy()
    if not _primera_vez(bd, f"diario:{hoy_.isoformat()}"):
        return {"ok": True, "repetido": True}
    texto, avisos = resumen_diario(bd, hoy_)
    informes = []
    if hoy_.weekday() == 0:  # lunes: análisis semanal + revisión
        for informe in (generar_financiero(bd, "semanal", hoy_), generar_revision(bd, hoy_)):
            informes.append(informe.tipo)
            texto += f"\n\n💡 <b>{TIPOS[informe.tipo]}</b>\n{escape(informe.texto or '')}"
    if hoy_.day == 1:
        informe = generar_financiero(bd, "mensual", hoy_)
        informes.append(informe.tipo)
        texto += f"\n\n📊 <b>Análisis de {escape(informe.periodo)}</b>\n{escape(informe.texto or '')}"
    bd.commit()
    enviado_ok = notificaciones.enviar(texto) if (avisos or informes) else False
    return {"ok": True, "avisos": avisos, "informes": informes, "telegram": enviado_ok}
