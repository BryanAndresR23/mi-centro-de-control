# Guía de uso y operación

Guía práctica en español para correr, probar, publicar y mantener la app. La presentación general del proyecto
está en el [README](../README.md).

## Scripts

| Script | Para qué sirve |
|---|---|
| `scripts/crear_credenciales.py` | Genera el hash de la contraseña, el secreto 2FA y `SECRET_KEY`. Con `--demo` usa la contraseña de prueba local |
| `scripts/datos_demo.py` | Carga 12 meses de datos de ejemplo en la base local, borrando lo que haya. Se niega a ejecutarse contra PostgreSQL |
| `scripts/capturas.py` | Genera las capturas del README con Edge sin ventana, usando solo la demo local |
| `scripts/generar_iconos.py` | Dibuja los íconos PNG de la app sin dependencias externas |
| `scripts/subir_github.py` | Hace el commit y lo sube a GitHub sin tener Git instalado (dulwich). `--lista` muestra qué se subiría y busca secretos |
| `scripts/configurar_vercel.py` | Ventana que configura Vercel por su API: proyecto, variables cifradas y despliegue |

## Uso diario

1. **Cuentas:** crea tus cuentas con el saldo que tienen *hoy*. En tarjetas y préstamos, el saldo es lo que debes.
2. **Movimientos:** registra con el botón **+**.
   - Pagar la tarjeta: **Transferencia** de tu cuenta a la tarjeta.
   - Guardar dinero: **Ahorro**.
3. **Revisión:** revisa el **Inicio** al final del mes.
4. **Respaldo:** descárgalo una vez al mes en *Más → Respaldo*.

## Funciones de las fases 1b a 4

| Dónde | Qué haces |
|---|---|
| **Hoy** (inicio) | Ves tareas vencidas y de hoy, la semana, pagos próximos, alertas de presupuesto, hábitos y fechas de becas y postulaciones. Agregas tareas rápidas y las marcas con el círculo |
| **Más → Presupuestos** | Pones un tope mensual por categoría (ves tu promedio de 3 meses como guía). Al registrar un gasto que cruza el 80 % o el 100 %, aparece un aviso |
| **Más → Metas de ahorro** | Creas la meta y, al registrar un **Ahorro**, eliges la meta. La tarjeta muestra el ritmo, la fecha estimada y la cuota necesaria |
| **Más → Deudas y suscripciones** | Registras pagos fijos. **Pagado** crea el movimiento y pasa al próximo vencimiento. Una vez al mes respondes «¿la usaste?» |
| **➕ → 📷 Desde foto** | Tomas o eliges una o varias fotos, o subes el XML de la factura. Revisas los datos y pulsas **Guardar gasto** o **Confirmar todos** |
| **Más → Importar estado de cuenta** | Subes CSV/XLSX, confirmas las columnas, desmarcas filas y pulsas **Importar**. Lo ya importado no se duplica |
| **Pendientes** | Abres un área y usas su sección: registrar entrenamiento y rutina, sesiones de estudio, postulaciones, becas con checklist, metas con hitos. **+ Área** crea una nueva |
| **Más → Asesor** | Generas el análisis semanal o mensual o la revisión semanal. Cada consejo dice en qué hecho se basa |

### Activar la IA y los recordatorios

1. **IA:** crea una clave en <https://console.anthropic.com> (prepago). Por defecto se usa `claude-opus-5-5` (unos
   3-5 centavos por foto); con `MODELO_IA=claude-haiku-5-5` cuesta mucho menos.
2. **Telegram, el bot:** en Telegram, escríbele a **@BotFather** y envía `/newbot`. Copia el token.
3. **Telegram, tu chat:** escríbele cualquier mensaje a tu bot. Luego, con el token ya guardado en Vercel, abre
   **Más → Asesor → Averiguar mi chat** para ver tu `TELEGRAM_CHAT_ID`.
4. **Guardar las claves:** ejecuta `scripts\configurar_vercel.py`. Pega el token de Vercel y, en el paso 4, la
   clave de Anthropic, el token del bot y tu chat ID. Deja vacíos Neon, la contraseña y el QR para conservarlos.
   La ventana genera `CRON_SECRET` y vuelve a desplegar.
5. **Probar:** en **Más → Asesor**, pulsa **Enviar mensaje de prueba**. Desde ese momento, cada día a las 07:00
   llega el resumen. Los lunes trae además el análisis semanal y la revisión, y el día 1, el mensual.

## Publicar sin la ventana (panel de Vercel)

1. **Neon:**
   - Crea un proyecto en *AWS US East 1*.
   - En **Connect**, copia la cadena *pooled* (host con `-pooler`) y la *direct* (sin `-pooler`).
2. **Credenciales:** ejecuta `scripts\crear_credenciales.py` y guarda los valores que imprime.
3. **Vercel:**
   - *Add New → Project → Import* del repositorio.
   - En *Settings → Environment Variables* agrega estas variables:

     | Variable | Valor |
     |---|---|
     | `ENTORNO` | `produccion` |
     | `DATABASE_URL` | La cadena *pooled* de Neon |
     | `DATABASE_URL_MIGRACIONES` | La cadena *direct* de Neon |
     | `SECRET_KEY` | Valor generado en el paso 2 |
     | `APP_PASSWORD_HASH` | Valor generado en el paso 2 |
     | `TOTP_SECRET` | Valor generado en el paso 2 |

4. **Despliegue:** ve a *Deployments → ⋯ → Redeploy*. Cada despliegue ejecuta `alembic upgrade head`.

Si falta alguna variable, la app no arranca a medias: muestra cuáles faltan, sin mostrar sus valores.

## Cambiar la contraseña o el 2FA

Vuelve a ejecutar `scripts\configurar_vercel.py` con la misma cadena de Neon.

- Se reemplazan las variables y se vuelve a desplegar.
- `SECRET_KEY` cambia, así que se cierran las sesiones abiertas.
- Borra de Google Authenticator la entrada anterior.
- **Nunca compartas capturas de la ventana mientras se ve el QR o la clave manual.**

## Seguridad

- **Contraseña:** solo se guarda su hash Argon2.
- **2FA:** obligatorio en producción. Cada código sirve una sola vez.
- **Bloqueo:** 5 intentos fallidos en 15 minutos bloquean esa IP durante 15 minutos.
- **Sesión:** cookie firmada `HttpOnly`, `Secure` y `SameSite=Lax`, con duración máxima de 30 días.
- **Formularios:** todos llevan token CSRF.
- **Cabeceras:**
  - CSP sin scripts en línea.
  - `X-Frame-Options: DENY`.
  - HSTS.
  - `no-store` en las páginas con datos.
- **Secretos:** solo en variables de entorno. `.env` está excluido de Git y de Vercel.
- **Restaurar un respaldo:** pide escribir `REEMPLAZAR` y se hace en una sola transacción.
- **Redes corporativas:** pueden inspeccionar el tráfico HTTPS. Usa la app desde datos móviles o desde tu red de
  casa.
