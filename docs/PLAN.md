# Plan aprobado — Mi Centro de Control

Aprobado el 16 de noviembre de 2026.

## Decisiones

| Tema | Decisión |
|---|---|
| Desarrollo | Carpeta propia, separada de cualquier proyecto de trabajo |
| Hosting | Nube con planes gratuitos: Vercel (Hobby) + PostgreSQL en Neon |
| Lectura de fotos | Claude Opus 5.5, cambiable con una variable de entorno |
| Recordatorios | Bot de Telegram, resumen diario cerca de las 7:00 (Quito) |
| Importación bancaria | Fase 2, con un estado de cuenta de ejemplo por banco |
| Extras SRI | Rubros de gastos personales deducibles, aviso de "consumidor final" y XML autorizado por clave de acceso |

## Arquitectura

```
Celular / PC (navegador; instalable en la pantalla de inicio)
   │ HTTPS + contraseña y 2FA
   ▼
Vercel ─ FastAPI (Python)
   ├─ páginas Jinja + HTMX + Chart.js
   ├─ lectura de recibos ──► Claude (visión) y servicio web del SRI
   └─ cron diario (~7:00 Quito) ──► Telegram
   ▼
Neon PostgreSQL (datos + fotos comprimidas)
```

- **Cálculos en funciones puras**, probadas sin base de datos. La IA no calcula cifras: solo redacta a partir de
  hechos calculados por el código.
- **Montos** en `NUMERIC(12,2)` / `Decimal`. **Fechas** en America/Guayaquil.
- **Migraciones** con Alembic.
- **Fotos**: se reducen en el celular (~300 KB) y se guardan en la base, protegidas por el login.

## Modelo de datos

**Finanzas**
- `cuentas`, `categorias`, `movimientos`
- `presupuestos`, `metas_ahorro`, `recurrentes`
- Apoyo: `adjuntos`, `extracciones`, `reglas_categoria`, `plantillas_importacion`

**Pendientes**
- `areas` (con plantilla y configuración propia), `tareas`, `metas_personales`
- Fitness: `rutinas`, `rutina_ejercicios`, `entrenamientos`, `series`
- Estudio: `sesiones_estudio`
- `postulaciones` + `postulacion_eventos`
- `becas` + `beca_requisitos`

**Sistema:** `informes`, `avisos_enviados`, `config`, `intentos_login`

## Fases

| Fase | Contenido | Estado |
|---|---|---|
| 1a | Login con 2FA, cuentas, categorías, movimientos, transferencias y ahorros, dashboard, respaldo, datos de ejemplo, despliegue | **Hecha** y en producción (Vercel + Neon, uso personal) |
| 1b | Presupuestos (alertas al 80 % y al 100 %), metas de ahorro, deudas y suscripciones, dashboard completo | Pendiente |
| 2 | Fotos de recibos (sueltas o en lote) con Claude, XML del SRI, importación CSV/Excel con detección de duplicados | Pendiente |
| 3 | Áreas con sus plantillas (trabajo, fitness, inglés, SQL, postulaciones, becas, metas), vista Hoy | Pendiente |
| 4 | Asesor semanal y mensual, revisión semanal, recordatorios por Telegram | Pendiente |

## Lo que necesitaré de ti

| Cuándo | Qué |
|---|---|
| Fin de la 1a | Cuentas en Neon y Vercel, token de GitHub, ejecutar `crear_credenciales.py` |
| Fase 2 | Clave de API de Anthropic con saldo, 5 a 10 recibos reales, un estado de cuenta por banco |
| Fase 4 | Bot de Telegram creado con @BotFather |
