# Build process · Proceso de construcción

[English](#english) · [Español](#español)

---

## English

How *Mi Centro de Control* went from a one-page brief to a production app: the steps, the decisions and the
problems found along the way.

### 1. Brief and questions

The brief asked for a personal app with two modules:
- **Finances:** movements, receipt scanning, budgets, goals, debts, a dashboard and an advisor.
- **To-dos and goals:** work, gym, English, SQL, job applications, scholarships and personal goals.

It had to work well on a phone, be private, be cheap, and be built in phases with tests.

Before any code, I compared running the app locally with deploying it:

| | Local | Deployed on free tiers |
|---|---|---|
| Cost | $0 | $0 hosting; only AI usage is paid |
| Phone use | Same Wi-Fi, PC on | Anywhere, with HTTPS |
| Reminders | Only while the PC is on | Always |
| Privacy | Data never leaves the PC | Data with a provider, so strong auth is required |

**Decision:** deploy it on Vercel + Neon, with a password and two-factor authentication.

### 2. Written plan, approved before coding

[PLAN.md](PLAN.md) defines:
- the architecture;
- the data model (table names in Spanish, so they can be queried from SQL or Power BI);
- the screens;
- five phases (1a, 1b, 2, 3, 4).

Budgets, goals and recurring payments were moved into Phase 1, because the "Today" view and the advisor depend on
them.

### 3. Phase 1a: implementation

Built in this order:

1. **Configuration and database:**
   - Settings come from environment variables.
   - The database engine is SQLite locally and PostgreSQL in production.
   - Constraint names are stable, so the same Alembic migrations work on both engines.
2. **Pure calculation module** (`calculos.py`), written and tested first:
   - balances, including credit cards as negative balances;
   - net worth and monthly summaries;
   - spending by category, with subcategories rolled up;
   - 12-month series and month-over-month change.
3. **Security** (`seguridad.py`):
   - Argon2 password hash.
   - TOTP codes that cannot be reused.
   - Login lockout per IP.
   - Session expiry and CSRF.
   - A redirect guard against open redirects.
4. **Pages:**
   - One form handles expense, income, transfer and saving.
   - Your most-used categories appear as chips, and expenses are suggested as SRI-deductible by category.
   - The list filters with HTMX and the dashboard draws its charts with Chart.js.
   - No inline scripts, so the Content Security Policy stays strict.
5. **Backup and restore:** generic across all tables, inside a single transaction. Self-referencing categories are
   restored correctly and PostgreSQL sequences are reset.
6. **Demo data:** 12 months of realistic movements in Quito. Credit-card payments are computed from the statement
   balance.

### 4. Quality checks and bugs fixed

The project has 78 pytest tests. Checks in a phone-sized browser and in automated screenshots caught these
problems, all fixed:

| Problem | Fix |
|---|---|
| `"0.005"` read as 5, and `"12..5"` accepted | Rewrote amount parsing with proper thousands-group validation |
| Demo generator passed "deductible" into the destination-account argument (foreign-key error) | Made that parameter keyword-only |
| Mid-month dashboard compared half a month against a full month ("income −100%") | Current month now compares against the same day of the previous month |
| Trend chart dropped to $0 for months before the first record | Leading empty months are trimmed |
| A form field stretched and lost alignment on mobile | `align-content: start` on form fields |

### 5. Deployment

Two constraints shaped the deployment:
- **No Git** on the development PC.
- **The browser could not be automated** in that environment.

How each step was solved:

1. **GitHub:** `scripts/subir_github.py` commits and pushes with `dulwich`, a pure-Python Git library.
   - It excludes `.env`, the local database and the virtual environment.
   - It blocks the push if any file contains a password hash, a TOTP secret, a database URL with a password, or a
     token.
   - The token is pasted into a hidden field and never stored.
2. **Hosting account:** the Vercel connection available in the development tools belonged to another person, so
   the app was deployed to the owner's own account instead. Whoever controls the hosting account can read the
   app's secrets.
3. **Vercel configuration by API:** `scripts/configurar_vercel.py` is a small desktop window that:
   - receives a Vercel token, the Neon connection string and the password;
   - shows a QR code for Google Authenticator and verifies the first code;
   - derives the pooled and direct Neon URLs;
   - stores six encrypted environment variables and starts a production deployment.

   One issue came up: new Vercel accounts work inside a "team", and a team-scoped token cannot read `/v2/user`. The
   window now finds the team ID through `/v2/teams`, or from the projects list.
4. **Migrations** run at build time (`alembic upgrade head`), through Neon's direct connection.
5. **Production check:** the login page asks for 2FA, static files load, HSTS is set, and a wrong password returns
   401. That last check also confirms that the database and tables work.

### 6. Lessons

- **Secrets in screenshots are secrets.** A 2FA QR code was shared in a screenshot. Re-running the setup window
  rotates the secret, and the docs now warn about it.
- **Corporate networks may inspect HTTPS.** The proxy rewrote the app's CSP header. Use personal finance apps from
  mobile data or a home network.
- **Test what users see.** The most useful bugs were found by looking at the real pages, not only by unit tests.

---

## Español

Cómo *Mi Centro de Control* pasó de un brief de una página a una app en producción: los pasos, las decisiones y
los problemas encontrados en el camino.

### 1. Brief y preguntas

El brief pedía una app personal con dos módulos:
- **Finanzas:** movimientos, lectura de recibos, presupuestos, metas, deudas, dashboard y asesor.
- **Pendientes y metas:** trabajo, gimnasio, inglés, SQL, postulaciones, becas y metas personales.

Debía funcionar bien en el celular, ser privada y barata, y construirse por fases con pruebas.

Antes de escribir código, comparé correrla en local contra publicarla:

| | Local | Publicada en planes gratuitos |
|---|---|---|
| Costo | $0 | $0 de hosting; solo se paga el uso de IA |
| Uso en el celular | Misma red Wi-Fi y PC prendida | Desde cualquier lugar, con HTTPS |
| Recordatorios | Solo con la PC prendida | Siempre |
| Privacidad | Los datos no salen de la PC | Datos en un proveedor, por eso hace falta un acceso fuerte |

**Decisión:** publicarla en Vercel + Neon, con contraseña y verificación en dos pasos.

### 2. Plan escrito, aprobado antes de programar

[PLAN.md](PLAN.md) define:
- la arquitectura;
- el modelo de datos (tablas en español, para consultarlas desde SQL o Power BI);
- las pantallas;
- cinco fases (1a, 1b, 2, 3, 4).

Los presupuestos, las metas y los pagos recurrentes se adelantaron a la Fase 1, porque la vista «Hoy» y el asesor
dependen de ellos.

### 3. Fase 1a: implementación

Se construyó en este orden:

1. **Configuración y base de datos:**
   - La configuración se lee de variables de entorno.
   - El motor es SQLite en local y PostgreSQL en producción.
   - Los nombres de las restricciones son estables, para que las mismas migraciones de Alembic sirvan en ambos.
2. **Módulo de cálculos puros** (`calculos.py`), escrito y probado primero:
   - saldos, con las tarjetas como saldo negativo;
   - patrimonio neto y resúmenes mensuales;
   - gasto por categoría, sumando las subcategorías;
   - series de 12 meses y variación contra el mes anterior.
3. **Seguridad** (`seguridad.py`):
   - Hash Argon2 de la contraseña.
   - Códigos TOTP que no se pueden reutilizar.
   - Bloqueo de acceso por IP.
   - Expiración de sesión y CSRF.
   - Protección contra redirecciones abiertas.
4. **Pantallas:**
   - Un solo formulario sirve para gasto, ingreso, transferencia y ahorro.
   - Las categorías que más usas aparecen como botones, y el gasto se sugiere como deducible del SRI según la
     categoría.
   - La lista se filtra con HTMX y el dashboard dibuja sus gráficos con Chart.js.
   - No hay scripts en línea, así la política de seguridad de contenido (CSP) se mantiene estricta.
5. **Respaldo y restauración:** genéricos para todas las tablas, en una sola transacción. Las categorías que
   dependen de otras se restauran bien y se reinician las secuencias de PostgreSQL.
6. **Datos de ejemplo:** 12 meses de movimientos realistas en Quito. Los pagos de la tarjeta se calculan según el
   saldo al corte.

### 4. Control de calidad y errores corregidos

El proyecto tiene 78 pruebas con pytest. Las revisiones en un navegador del tamaño de un celular y las capturas
automáticas detectaron estos problemas, todos corregidos:

| Problema | Solución |
|---|---|
| `"0.005"` se leía como 5 y se aceptaba `"12..5"` | Se reescribió la lectura de montos, validando los grupos de miles |
| El generador de la demo pasaba «deducible» como cuenta destino (error de clave foránea) | Ese parámetro ahora solo se acepta por nombre |
| A mitad de mes se comparaba medio mes contra un mes completo («ingresos −100 %») | El mes en curso se compara contra el mismo día del mes anterior |
| El gráfico de tendencia caía a $0 antes del primer registro | Se omiten los meses vacíos del inicio |
| Un campo del formulario se estiraba y quedaba desalineado en el celular | `align-content: start` en los campos |

### 5. Despliegue

Dos limitaciones marcaron el despliegue:
- La PC de desarrollo **no tenía Git**.
- **No se podía automatizar el navegador** en ese entorno.

Así se resolvió cada paso:

1. **GitHub:** `scripts/subir_github.py` hace el commit y lo sube con `dulwich`, una librería de Git en Python
   puro.
   - Excluye `.env`, la base local y el entorno virtual.
   - Bloquea la subida si algún archivo contiene un hash de contraseña, un secreto TOTP, una URL de base de datos
     con contraseña o un token.
   - El token se pega en un campo oculto y no se guarda.
2. **Cuenta de hosting:** la conexión de Vercel disponible en las herramientas de desarrollo era de otra persona,
   así que se publicó en la cuenta propia del dueño. Quien controla la cuenta de hosting puede leer los secretos
   de la app.
3. **Configuración de Vercel por API:** `scripts/configurar_vercel.py` es una ventana de escritorio que:
   - recibe el token de Vercel, la cadena de conexión de Neon y la contraseña;
   - muestra un código QR para Google Authenticator y verifica el primer código;
   - deriva las URL de Neon con pooler y directa;
   - guarda seis variables de entorno cifradas y lanza el despliegue de producción.

   Hubo un problema: las cuentas nuevas de Vercel funcionan como un «equipo», y un token de equipo no puede leer
   `/v2/user`. La ventana ahora obtiene el ID del equipo con `/v2/teams` o desde la lista de proyectos.
4. **Migraciones:** corren al compilar (`alembic upgrade head`), por la conexión directa de Neon.
5. **Verificación en producción:** la página de acceso pide 2FA, los archivos estáticos cargan, HSTS está activo y
   una contraseña incorrecta devuelve 401. Esto último también confirma que la base y las tablas funcionan.

### 6. Lecciones

- **Un secreto en una captura sigue siendo un secreto.** Se compartió un QR de 2FA en una captura. Volver a
  ejecutar la ventana de configuración cambia el secreto, y la documentación ahora lo advierte.
- **Las redes corporativas pueden inspeccionar HTTPS.** El proxy reescribía la cabecera CSP de la app. Usa las
  apps de finanzas personales con datos móviles o en tu red de casa.
- **Hay que probar lo que ve el usuario.** Los errores más útiles salieron de mirar las páginas reales, no solo
  de las pruebas unitarias.
