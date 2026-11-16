# Mi Centro de Control

App web personal para llevar tus finanzas, pendientes y metas, pensada para usarla desde el celular.
Está en español y trabaja en USD con la hora de Quito.

**Estado: Fase 1a.** Ya funcionan el login con 2FA, las cuentas, las categorías (con rubros del SRI), los movimientos
(gastos, ingresos, transferencias y ahorros), el dashboard y el respaldo completo. Las fases siguientes están en
[docs/PLAN.md](docs/PLAN.md).

## Stack

| Pieza | Uso |
|---|---|
| Python 3.13 + FastAPI | Servidor y lógica |
| Jinja + HTMX + Chart.js | Páginas generadas en el servidor, casi sin JavaScript |
| SQLAlchemy + Alembic | Modelo de datos y migraciones |
| SQLite (local) / PostgreSQL en Neon (producción) | Base de datos |
| Vercel (plan Hobby) | Hosting gratuito con HTTPS |
| Argon2 + TOTP (Google Authenticator) | Contraseña y verificación en dos pasos |

## Probar en tu PC

En PowerShell, desde esta carpeta:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe scripts\crear_credenciales.py --demo
.\.venv\Scripts\python.exe scripts\datos_demo.py
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Abre <http://localhost:8000> y entra con la contraseña de la demo: **`demo-centro-2026`**.
`datos_demo.py` carga 12 meses de movimientos de ejemplo en la base local (`datos_locales.db`) y borra lo que
hubiera. Nunca se ejecuta contra la base de producción.

Para usar tu propia contraseña y 2FA en local:

```powershell
.\.venv\Scripts\python.exe scripts\crear_credenciales.py
```

El script te pide la contraseña (mínimo 12 caracteres), muestra un código QR para Google Authenticator, verifica
el primer código y guarda en `.env` solo el hash, nunca la contraseña. También imprime los valores que luego
pegarás en Vercel.

## Pruebas

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Cubren:
- **Montos**: formatos como `1.234,56`, `1,234.56` y `$ 12`, y redondeo exacto con `Decimal`.
- **Cálculos**: saldos, tarjetas, patrimonio, tasa de ahorro, gasto por categoría y series de 12 meses.
- **Seguridad**: login, 2FA, códigos que no se pueden reutilizar, bloqueo por intentos, CSRF y cabeceras.
- **Formularios**: movimientos, cuentas y categorías, con sus validaciones.
- **Respaldo**: exportar y restaurar sin perder nada, y revertir si el archivo es inválido.
- **Datos de ejemplo**: que sean coherentes.
- **Despliegue**: que las dependencias coincidan entre `pyproject.toml` y `requirements.txt`.

## Cómo piensa la app (reglas contables)

- **Saldo inicial.** Cada cuenta parte del saldo que tenía al empezar un día. Los movimientos desde ese día se
  suman. Si registras movimientos anteriores, sirven para el historial y no cambian el saldo de hoy.
- **Tarjetas y préstamos** son pasivos: su saldo inicial es lo que debes. Un gasto con tarjeta aumenta la deuda.
- **Pagar la tarjeta** es una *transferencia* de tu cuenta a la tarjeta, no un gasto. El gasto ya se contó cuando
  compraste, así no se cuenta dos veces.
- **Ahorrar** es una transferencia a una cuenta de ahorros o inversión: no es gasto y no cambia tu patrimonio.
- **Flujo del mes** = ingresos − gastos. **Tasa de ahorro** = flujo ÷ ingresos. **Patrimonio neto** = lo que
  tienes − lo que debes.
- A mitad de mes, el dashboard se compara con el mes anterior **hasta el mismo día**.
- **Montos**: siempre `NUMERIC(12,2)` / `Decimal`, nunca `float`.
- **Rubros SRI** (vivienda, educación, salud, vestimenta, alimentación, turismo): son una sugerencia por categoría
  para tu proyección de gastos personales. Al elegir una categoría con rubro, el gasto se marca como deducible
  y puedes desmarcarlo. Revisa las reglas vigentes del SRI.

## Estructura

```
app/
  main.py            FastAPI: seguridad, sesión, rutas
  config.py          variables de entorno
  models.py          tablas (en español, para SQL y Power BI)
  seguridad.py       Argon2, TOTP, límite de intentos, CSRF
  respaldo.py        exportar / restaurar (genérico para todas las tablas)
  demo.py            datos de ejemplo
  finanzas/          cálculos puros (calculos.py), montos, consultas
  rutas/             páginas: inicio, movimientos, cuentas, categorías, respaldo, login
  templates/ static/ HTML, CSS, JS, íconos y manifiesto (instalable en el celular)
migrations/          Alembic
scripts/             credenciales, datos de ejemplo, íconos, subida a GitHub
tests/               pytest
```

## Publicar en internet (Neon + GitHub + Vercel)

**En producción:** <https://mi-centro-de-control.vercel.app>, publicada el 16 de noviembre de 2026.

### Vía rápida (la que se usó): `scripts\configurar_vercel.py`

```powershell
.\.venv\Scripts\python.exe scripts\configurar_vercel.py
```

Abre una ventana donde:
1. Pegas un token de Vercel.
2. Pegas la cadena de conexión de Neon.
3. Escribes tu contraseña y escaneas un QR nuevo de Google Authenticator.

La ventana se encarga del resto:
- Crea o reutiliza el proyecto ligado a tu repo de GitHub.
- Guarda las 6 variables cifradas. La URL directa para las migraciones la deriva de la de Neon.
- Despliega a producción.

Los secretos van directo de la ventana a Vercel. No se guardan en disco y no aparecen en
`configurar_vercel.log`, que solo anota los pasos.

Sirve también para **cambiar la contraseña o el 2FA**: vuelve a ejecutarla. `SECRET_KEY` cambia, así que se
cierran las sesiones abiertas. **No compartas capturas de la ventana mientras se ve el QR.**

### Vía manual (panel de Vercel)

Todo es gratis. Las cuentas las creas tú.

1. **Neon** (<https://neon.tech>): crea un proyecto en la región *AWS US East (N. Virginia)*, que es la misma
   región donde Vercel ejecuta las funciones por defecto. En *Connect* copia dos cadenas de conexión:
   - **Pooled connection**, cuyo host incluye `-pooler`. Será `DATABASE_URL`.
   - **Direct connection**, sin `-pooler`. Será `DATABASE_URL_MIGRACIONES`.
2. **Credenciales**: ejecuta `scripts\crear_credenciales.py` (sin `--demo`) y guarda los tres valores que imprime.
3. **GitHub**: esta PC no tiene Git, así que el código se sube con dulwich:
   ```powershell
   .\.venv\Scripts\python.exe scripts\subir_github.py --lista
   .\.venv\Scripts\python.exe scripts\subir_github.py "Fase 1a"
   ```
   El primer comando solo muestra qué se subiría. El segundo crea el commit, abre una ventana donde pegas un token
   de GitHub con permiso `repo` (oculto, no se guarda), crea el repositorio **privado** y sube el código. Antes
   de subir, el script revisa que ningún archivo tenga secretos y nunca incluye `.env` ni la base local.
4. **Vercel** (<https://vercel.com>): *Add New → Project → Import* del repositorio. Vercel detecta FastAPI
   (`app/main.py`). Antes de desplegar, en *Environment Variables* agrega:

   | Variable | Valor |
   |---|---|
   | `ENTORNO` | `produccion` |
   | `DATABASE_URL` | la cadena *pooled* de Neon |
   | `DATABASE_URL_MIGRACIONES` | la cadena *direct* de Neon |
   | `SECRET_KEY`, `APP_PASSWORD_HASH`, `TOTP_SECRET` | los valores del paso 2 |

   Cada despliegue aplica las migraciones (`alembic upgrade head`) antes de publicar.
5. En el celular, abre `https://<tu-proyecto>.vercel.app`, entra con tu contraseña y el código de Authenticator,
   y en el menú del navegador elige **Agregar a pantalla de inicio**.

Para publicar cambios después, vuelve a ejecutar `subir_github.py`. Vercel despliega solo con cada push.

Si falta alguna variable, la app no arranca a medias: muestra qué variables faltan, sin mostrar sus valores.

## Seguridad y privacidad

- **Contraseña**: se guarda solo con hash Argon2. En producción el 2FA es obligatorio y cada código sirve una
  sola vez.
- **Intentos fallidos**: 5 intentos en 15 minutos bloquean esa IP durante 15 minutos.
- **Sesión**: cookie firmada `HttpOnly`, `Secure` y `SameSite=Lax`, que dura 30 días.
- **Formularios**: todos llevan token CSRF.
- **Cabeceras**: CSP estricta (sin scripts en línea), `X-Frame-Options: DENY`, HSTS y `no-store` en las páginas
  con datos.
- **Secretos**: solo en variables de entorno. `.env` está en `.gitignore` y `.vercelignore`.
- **Respaldo** (*Más → Respaldo*): un ZIP con `datos.json` para restaurar y un CSV por tabla para Excel o Power
  BI. Restaurar reemplaza todo, pide escribir `REEMPLAZAR` y se hace en una sola transacción: si algo falla, no
  cambia nada.
