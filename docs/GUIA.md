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
