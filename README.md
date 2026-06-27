# WhaConnect

Panel y API para gestión de licencias de bots de notificaciones WhatsApp, con sistema de pagos, activación por licencia y entrega de instaladores automáticos. Pensado como producto para revender acceso a bots de WhatsApp.

**Arquitectura:**
- **API** (`api/main.py`) — FastAPI: gestiona licencias, clientes, pagos (MercadoPago + Ualá), envío de emails con instaladores, y validación de tokens.
- **Frontend** (`frontend/`) — interfaz web estática (HTML + assets) servida al cliente.
- **Base de datos** — PostgreSQL (licencias, clientes, pagos).

---

## Requisitos del servidor

- **Sistema:** Ubuntu / Debian (probado en VPS)
- **Python:** versión 3.10 o superior
- **PostgreSQL:** base de datos
- **pm2:** para mantener la API corriendo
- Credenciales de: **MercadoPago**, **Ualá**, **DeepSeek**, y un **servidor SMTP** para emails
- Opcional: credencial de **Google TTS** (`google-tts.json`) si se usa el módulo de voz

---

## Instalación paso a paso

### 1. Clonar el repositorio

```bash
cd /opt
git clone https://github.com/Agro-bot2026/whaconnect.git whaconnect
cd whaconnect/api
```

### 2. Instalar dependencias del sistema (si el VPS es nuevo)

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip postgresql nodejs npm
sudo npm install -g pm2
```

### 3. Crear la base de datos PostgreSQL

```bash
sudo -u postgres psql
```

Dentro de psql, creá el usuario y la base (poné una contraseña propia y fuerte):

```sql
CREATE USER wha WITH PASSWORD 'tu_password_segura';
CREATE DATABASE whaconnect OWNER wha;
\q
```

### 4. Crear el entorno virtual e instalar dependencias

```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 5. Colocar la credencial de Google TTS (si se usa)

El archivo `google-tts.json` no viene en el repo. Si usás el módulo de voz, copialo desde tu backup:

```bash
# debe quedar en: /opt/whaconnect/api/google-tts.json
```

### 6. Configurar el archivo `.env` (¡el paso clave!)

Toda la configuración se lee del `.env`, que no viene en el repo. Creá el archivo en `/opt/whaconnect/api/.env`:

```bash
nano /opt/whaconnect/api/.env
```

Completá con tus valores reales:

```
# --- Base de datos ---
DATABASE_URL=postgresql://wha:tu_password_segura@localhost/whaconnect

# --- URL pública ---
PUBLIC_URL=https://wha.connect-vpn.top

# --- MercadoPago ---
MP_ACCESS_TOKEN=tu_access_token_de_mercadopago
MP_PRICE=valor_del_plan

# --- Ualá (pagos) ---
UALA_CLIENT_ID=tu_client_id
UALA_CLIENT_SECRET=tu_client_secret
UALA_USERNAME=tu_usuario_uala

# --- DeepSeek (IA) ---
DEEPSEEK_API_KEY=tu_api_key_de_deepseek

# --- Email / SMTP ---
SMTP_HOST=smtp.tuservidor.com
SMTP_PORT=587
SMTP_USER=tu_usuario_smtp
SMTP_PASSWORD=tu_password_smtp
SMTP_FROM_EMAIL=noreply@tudominio.com
SMTP_FROM_NAME=WhaConnect

# --- Seguridad ---
JWT_SECRET=una_clave_larga_y_aleatoria
ADMIN_SECRET=otra_clave_larga_y_aleatoria
ADMIN_PASSWORD=tu_password_de_admin
```

> Guardá con `Ctrl+O`, Enter, y salí con `Ctrl+X`.
> `JWT_SECRET` y `ADMIN_SECRET`: si no los definís, el sistema genera uno aleatorio al arrancar, pero conviene fijarlos propios para que no cambien en cada reinicio.
>
> ---

## Arrancar la API

La API se ejecuta con **pm2** usando uvicorn, en el puerto 8000.

```bash
cd /opt/whaconnect/api
pm2 start "venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000" --name wha-api
pm2 save
```

> En producción conviene poner nginx por delante como reverse proxy (dominio + SSL). El frontend estático de `frontend/` se sirve desde ahí.

---

## Comandos útiles de pm2

| Acción | Comando |
|---|---|
| Ver procesos | `pm2 list` |
| Ver logs | `pm2 logs wha-api` |
| Reiniciar | `pm2 restart wha-api` |
| Detener | `pm2 stop wha-api` |

---

## Estructura del proyecto

```
whaconnect/
├── api/
│   ├── main.py            # API FastAPI (licencias, pagos, emails)
│   ├── database.py        # Conexión a PostgreSQL (lee DATABASE_URL del .env)
│   ├── models.py          # Modelos de la base de datos
│   ├── uala.py            # Integración de pagos Ualá
│   ├── tts.py             # Texto a voz
│   ├── vision.py          # Procesamiento de imágenes
│   ├── install-bot.sh     # Script instalador que se entrega al cliente
│   ├── requirements.txt   # Dependencias de Python
│   ├── .env               # ⚠️ NO incluido - todas las credenciales
│   └── google-tts.json    # ⚠️ NO incluido - credencial de Google TTS
├── frontend/              # Interfaz web estática (HTML, CSS, JS, logo)
├── docs/                  # Documentación e instaladores
├── uploads/               # ⚠️ NO incluido - archivos de usuarios
├── backups/               # ⚠️ NO incluido - respaldos
└── cmd_files/             # ⚠️ NO incluido - configs .hc, audios, guías (datos)
```

---

## Archivos que NO vienen en el repo

Por seguridad y peso, estos quedan fuera de GitHub y se aportan al reinstalar:

- **`api/.env`** — TODAS las credenciales (base de datos, MercadoPago, Ualá, DeepSeek, SMTP)
- **`api/google-tts.json`** — credencial de Google TTS
- **`uploads/`** — archivos de usuarios
- **`backups/`** — respaldos
- **`cmd_files/`** — configuraciones `.hc`, audios y guías que se distribuyen a clientes (copialos del backup completo)
- **`venv/`** — entorno virtual (se regenera con `pip install -r requirements.txt`)
- La **base de datos PostgreSQL** — se restaura desde un dump del backup

---

## Nota de seguridad

Este proyecto maneja **pagos reales (MercadoPago y Ualá) y datos de clientes**. Al reinstalar:
- Nunca subas el `.env` ni un dump de la base de datos a ningún repositorio.
- Usá una contraseña propia y fuerte para PostgreSQL (no reutilices ejemplos).
- Si alguna credencial se expuso alguna vez, rotála desde su panel correspondiente.
- Recordá: el código va en GitHub; el backup completo (con `cmd_files`, uploads y la base) va guardado aparte, fuera del VPS.
