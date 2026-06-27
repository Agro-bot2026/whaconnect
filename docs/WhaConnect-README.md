# WhaConnect — Referencia técnica

Plataforma SaaS para vender acceso a un **bot de WhatsApp self-hosted**. El cliente paga
una vez, instala el bot en su propio VPS con un comando, y lo gestiona desde un panel web.

- **Dominio:** https://wha.connect-vpn.top
- **VPS:** Ubuntu 24.04 LTS (`vps3418977`, IP 157.250.202.243)
- **Precio:** $10.000 ARS — pago único (Mercado Pago)
- **Candado:** licencia atada a la IP del VPS del cliente (1 cambio de IP gratis)

---

## Arquitectura

Dos máquinas distintas:

1. **Tu VPS** aloja: panel web (React), API (FastAPI), base de datos (PostgreSQL),
   endpoint que sirve el instalador, y guarda temporalmente los archivos de los clientes.
2. **El VPS del cliente** aloja: su bot de WhatsApp (Node.js + Baileys). Cada cliente
   hostea el suyo. Tu VPS solo vende, licencia y reparte — no corre los bots de los clientes.

### Stack
- **API:** FastAPI + Uvicorn (Python) — bajo PM2 como `wha-api`
- **Base de datos:** PostgreSQL 16 (base `whaconnect`, usuario `wha`)
- **Frontend:** React + Vite + Tailwind (compilado, servido como estático)
- **Reverse proxy + HTTPS:** Caddy (certificado automático)
- **Pagos:** Mercado Pago (preferencias con `notification_url` propio)
- **Email:** SMTP de Spacemail (magic link)
- **Bot del cliente:** Node 20 + Baileys + PM2

---

## Rutas en el VPS

| Ruta | Qué es |
|---|---|
| `/opt/whaconnect/api/` | Código de la API (main.py, models.py, database.py) |
| `/opt/whaconnect/api/venv/` | Entorno virtual de Python |
| `/opt/whaconnect/api/.env` | Credenciales (MP, SMTP, JWT, admin) — **chmod 600** |
| `/opt/whaconnect/api/install-bot.sh` | Plantilla del instalador que baja el cliente |
| `/opt/whaconnect/frontend/` | Frontend compilado que sirve Caddy |
| `/opt/whaconnect/uploads/{license_id}/` | Archivos de cada cliente (separados por licencia) |
| `/etc/caddy/Caddyfile` | Config de Caddy |
| En el cliente: `/opt/whaconnect-bot/` | El bot instalado |

---

## Flujo de compra (automático)

1. Cliente entra a la web → elige el plan → ingresa **email + IP** de su VPS.
2. Paga en Mercado Pago.
3. El **webhook** (`/mp/webhook`) recibe la confirmación → activa la licencia.
4. La pantalla de éxito muestra el **token** y el comando de instalación.
5. El cliente corre el comando en su VPS:
   ```
   bash <(curl -s https://wha.connect-vpn.top/install/SU_TOKEN)
   ```
6. El instalador valida token + IP, instala Node/PM2/Baileys, escribe el bot.
7. El cliente vincula WhatsApp con el comando `whabot` (escanea el QR).

### Candado de licencia
- El instalador valida contra `/api/validate/{token}`: compara la IP de origen con la registrada.
- Primera instalación: **fija** (bindea) la IP.
- El bot revalida la licencia cada 6 h. Si se revoca o la IP no coincide, no arranca.

---

## Comandos del cliente (en su VPS)

| Comando | Acción |
|---|---|
| `whabot` | Vincular WhatsApp (muestra el QR en la terminal) |
| `whabot reset` | Borra la sesión y genera un QR nuevo |
| `whabot fondo` | Corre el bot en segundo plano (PM2) |
| `whabot stop` | Detiene el bot |
| `whabot logs` | Ver logs del bot |

**No ocupa puertos entrantes** — Baileys es cliente saliente, convive sin conflicto con
ADMRufu u otros servicios.

---

## Funciones del bot

### Comandos que crea el cliente (panel → Comandos)
- **Respuesta:** trigger → texto. Ej: `/menu` responde un texto.
- **Acción** (el cliente elige el trigger y la acción):
  - Expulsar del grupo *(responder al mensaje del objetivo)*
  - Promover / quitar admin *(responder)*
  - Silenciar / abrir grupo
  - Etiquetar a todos
  - Advertencia (warn) *(responder)*
  - Enviar archivo guardado
  - Borrar mensaje *(responder)*

> Para moderar, **el bot debe ser administrador del grupo**.

### Acciones automáticas (panel → Acciones Automáticas)
- **Bienvenida:** texto personalizable con `{usuario}` y `{grupo}`, opción de foto de perfil.
- **Despedida:** texto personalizable.
- **Antilink:** borra mensajes con links. Acción: borrar / advertir / expulsar. Admins exentos.
- **Antispam:** umbral (X mensajes en Y segundos). Acción: borrar / advertir. Admins exentos.

### Archivos (panel → Archivos)
- El cliente sube archivos (máx **16 MB**) → el bot los descarga a su VPS.
- Por WhatsApp: `/listar archivos` (lista) y `/archivos` (los envía todos).
- Eliminar desde el panel borra el archivo también en el bot.
- Atados a cada licencia: **nunca se mezclan entre clientes**.

### Sincronización
El bot consulta tu API cada 2 minutos: comandos, settings y archivos. Los cambios del
panel se aplican en hasta 2 minutos.

---

## Panel de administración

Acceso: `https://wha.connect-vpn.top/admin` (login con contraseña de admin).

- **Métricas:** ingresos, clientes, licencias (activas/pendientes/revocadas), torta, conversión.
- **Clientes:** lista con búsqueda.
- **Licencias:** revocar, activar (venta manual), cambiar IP.
- **Pagos:** historial de Mercado Pago.

---

## Operación y mantenimiento

### Ver estado de la API
```
pm2 list
pm2 logs wha-api --lines 30
pm2 restart wha-api
```

### Recargar Caddy (tras cambios de config)
```
systemctl reload caddy
```

### Actualizar el frontend (nuevo build)
1. Subir el `.tar.gz` a `/opt/whaconnect/` por SFTP.
2. ```
   rm -rf /opt/whaconnect/frontend/* && cd /opt/whaconnect/frontend && tar -xzf /opt/whaconnect/ARCHIVO.tar.gz
   ```

### Base de datos
```
sudo -u postgres psql -d whaconnect
```
Tablas: `clients`, `licenses`, `commands`, `payments`, `magic_links`, `bot_files`, `settings`.

### Variables en `.env`
`MP_ACCESS_TOKEN`, `MP_PRICE`, `PUBLIC_URL`, `SMTP_*`, `JWT_SECRET`, `ADMIN_PASSWORD`, `ADMIN_SECRET`.

---

## Endpoints principales de la API

| Método | Ruta | Uso |
|---|---|---|
| GET | `/api/validate/{token}` | Valida licencia + IP (instalador y bot) |
| GET | `/install/{token}` | Sirve el instalador con el token inyectado |
| POST | `/api/buy` | Crea licencia pendiente + link de pago MP |
| POST | `/mp/webhook` | Activa licencia al aprobarse el pago |
| GET | `/api/license/{id}` | Token + comando de instalación |
| POST | `/api/login` | Envía magic link |
| GET | `/api/verify/{token}` | Verifica magic link, crea sesión |
| GET/POST/PUT/DELETE | `/api/commands` | CRUD de comandos (cliente) |
| GET/PUT | `/api/settings` | Acciones automáticas (cliente) |
| GET/POST/DELETE | `/api/files` | Archivos (cliente) |
| POST | `/api/change-ip` | Cambio de IP (1 gratis) |
| GET | `/api/bot/commands/{token}` | El bot baja sus comandos |
| GET | `/api/bot/settings/{token}` | El bot baja su config |
| GET | `/api/bot/files/{token}` | El bot lista/descarga sus archivos |
| POST | `/api/admin/login` | Login admin |
| GET | `/api/admin/metrics\|clients\|licenses\|payments` | Datos del admin |
| POST | `/api/admin/licenses/{id}/revoke\|activate\|change-ip` | Gestión de licencias |

---

## Novedades (última sesión)

- **Email post-pago:** al aprobarse el pago, el webhook envía automáticamente un correo
  al cliente con su token, IP registrada y comando de instalación.
- **Instalador con dependencias base:** ahora instala `git`, `curl`, `build-essential`,
  `ca-certificates` y `python3` solo, además de Node y PM2.
- **Sync del bot acelerado:** comandos y settings cada 30 seg, archivos cada 60 seg.
- **IA con DeepSeek en el panel:** botón "Generar con IA" (crea trigger + respuesta desde
  una descripción) y "Mejorar texto" (reescribe la respuesta). Key en `.env` como
  `DEEPSEEK_API_KEY`. Endpoints: `/api/ai/generate` y `/api/ai/improve`. Sin límite de uso
  por ahora — vigilar costo.
- **Pantalla de pago exitoso** pulida (sin datos de ejemplo, pasos con `whabot`).
- **Crear licencia manual (admin):** botón en Panel admin → Licencias → "Crear licencia
  manual". Pide email + IP, genera token, deja la licencia activa sin pago y muestra el
  comando de instalación. Endpoint: `/api/admin/licenses/create`. Sirve para cortesías,
  pruebas o usar WhaConnect uno mismo como cliente.

### Instalador personal (llave maestra)

Script privado para instalar el bot en cualquier VPS propio **sin licencia ni IP**.

- Ubicación (NO accesible desde la web): `/opt/whaconnect/docs/install-personal.sh`
- Instala en `/opt/whabot-personal/`, comando de control: `whabot-personal`
- Config local en `/opt/whabot-personal/comandos.json` (recarga sola cada 3 seg).
- No usa el panel — es autónomo. **No compartir este script.**

Uso: copiar el script al VPS destino → `bash install-personal.sh` →
editar `comandos.json` → `whabot-personal` para vincular.

---

## Pendientes / mejoras futuras

- **Prueba de pago real** ✅ hecha (pago real activó licencia y bot funcionó).
- **Programar mensajes** (requiere que el bot reporte sus grupos al panel).
- Métricas reales de uso del bot (mensajes, uptime) — hoy no se trackean.
- Limpieza automática de archivos en tu VPS una vez que el bot los descargó.
- Considerar HTTPS/seguridad extra y backups de la base de datos.

---

*Documento de referencia interno — WhaConnect*
