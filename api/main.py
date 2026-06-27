from fastapi import FastAPI, Depends, Request, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime
import secrets
from database import engine, get_db, Base
import models
try:
    import tts
except Exception as _e:
    tts = None
    print('TTS no disponible:', _e)

Base.metadata.create_all(bind=engine)

app = FastAPI(title="WhaConnect API")

BOT_VERSION_ACTUAL = "1.0.0"  # subir este número al publicar mejoras

def client_ip(req: Request):
    xff = req.headers.get("x-forwarded-for")
    return xff.split(",")[0].strip() if xff else req.client.host

@app.get("/")
def root():
    return {"service": "WhaConnect", "status": "ok"}

# Validacion de licencia: la usa el instalador y el bot
@app.get("/api/validate/{token}")
def validate(token: str, request: Request, db: Session = Depends(get_db), v: str = ""):
    lic = db.query(models.License).filter(models.License.token == token).first()
    if not lic:
        raise HTTPException(404, "Licencia inexistente")
    # registrar actividad y versión del bot
    lic.last_seen = datetime.utcnow()
    if v:
        lic.bot_version = v
    db.commit()
    if lic.status == "revoked":
        return {"ok": False, "reason": "revoked"}
    if lic.status != "active":
        return {"ok": False, "reason": "inactive"}

    ip = client_ip(request)
    # primera instalacion: fija la IP (trust on first use sobre la registrada)
    if not lic.ip_bound:
        if lic.ip_registered and ip != lic.ip_registered:
            return {"ok": False, "reason": "ip_mismatch_registered",
                    "registered": lic.ip_registered, "seen": ip}
        lic.ip_bound = ip
        db.commit()
        return {"ok": True, "bound": ip, "first": True}
    # instalaciones siguientes: debe coincidir con la fijada
    if ip != lic.ip_bound:
        return {"ok": False, "reason": "ip_mismatch_bound",
                "bound": lic.ip_bound, "seen": ip}
    return {"ok": True, "bound": lic.ip_bound}

from fastapi.responses import PlainTextResponse

@app.get("/install/{token}", response_class=PlainTextResponse)
def serve_installer(token: str, request: Request, db: Session = Depends(get_db)):
    lic = db.query(models.License).filter(models.License.token == token).first()
    if not lic or lic.status != "active":
        return PlainTextResponse("echo 'Licencia invalida o inactiva.'; exit 1", status_code=403)
    try:
        with open("/opt/whaconnect/api/install-bot.sh") as f:
            script = f.read()
    except FileNotFoundError:
        return PlainTextResponse("echo 'Instalador no disponible.'; exit 1", status_code=500)
    api_base = f"https://{request.headers.get('host', 'wha.connect-vpn.top')}"
    script = script.replace("__TOKEN__", token).replace("__API_BASE__", api_base)
    return script

import os, secrets as _secrets
from dotenv import load_dotenv
import mercadopago
from pydantic import BaseModel

load_dotenv("/opt/whaconnect/api/.env")
MP_TOKEN = os.getenv("MP_ACCESS_TOKEN")
MP_PRICE = int(os.getenv("MP_PRICE", "10000"))
PUBLIC_URL = os.getenv("PUBLIC_URL", "https://wha.connect-vpn.top")
sdk = mercadopago.SDK(MP_TOKEN)

class BuyReq(BaseModel):
    email: str
    ip: str
    password: str = ""
    accept_terms: bool = False

@app.post("/api/buy")
def buy(req: BuyReq, db: Session = Depends(get_db)):
    # crea o reutiliza cliente
    if not req.accept_terms:
        raise HTTPException(400, "Debés aceptar los términos y condiciones")
    if not req.password or len(req.password) < 6:
        raise HTTPException(400, "La contraseña debe tener al menos 6 caracteres")
    cli = db.query(models.Client).filter(models.Client.email == req.email).first()
    if not cli:
        cli = models.Client(email=req.email)
        db.add(cli); db.commit(); db.refresh(cli)
    # guardar/actualizar contraseña (encriptada)
    cli.password_hash = pwd_ctx.hash(req.password)
    db.commit()
    # crea licencia pendiente con la IP registrada
    token = _secrets.token_hex(16)
    lic = models.License(token=token, client_id=cli.id, status="pending",
                         ip_registered=req.ip, ip_changes_used=0)
    db.add(lic); db.commit(); db.refresh(lic)

    pref = {
        "items": [{"title": "Acceso WhaConnect (pago unico)",
                   "quantity": 1, "unit_price": MP_PRICE, "currency_id": "ARS"}],
        "payer": {"email": req.email},
        "external_reference": str(lic.id),
        "notification_url": f"{PUBLIC_URL}/mp/webhook",
        "back_urls": {"success": f"{PUBLIC_URL}/payment-success?lic={lic.id}"},
        "auto_return": "approved",
    }
    res = sdk.preference().create(pref)
    return {"init_point": res["response"]["init_point"], "license_id": lic.id}

@app.post("/mp/webhook")
async def mp_webhook(request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    pid = None
    if data.get("type") == "payment":
        pid = data.get("data", {}).get("id")
    elif data.get("topic") == "payment":
        pid = data.get("resource")
    if not pid:
        return {"ok": True}
    info = sdk.payment().get(pid)
    pay = info.get("response", {})
    if pay.get("status") == "approved":
        lic_id = pay.get("external_reference")
        lic = db.query(models.License).filter(models.License.id == int(lic_id)).first()
        if lic and lic.status != "active":
            lic.status = "active"
            db.add(models.Payment(license_id=lic.id, mp_payment_id=str(pid),
                                  amount=int(pay.get("transaction_amount", 0)), status="approved"))
            db.commit()
            # enviar email con el token y el comando de instalacion
            try:
                cli = db.query(models.Client).filter(models.Client.id == lic.client_id).first()
                if cli:
                    cmd = f"bash <(curl -s {PUBLIC_URL}/install/{lic.token})"
                    html = (f"<h2>¡Gracias por tu compra en WhaConnect!</h2>"
                            f"<p>Tu acceso ya está activo. Token de licencia:</p>"
                            f"<p style='font-family:monospace;background:#f0f0f0;padding:8px'>{lic.token}</p>"
                            f"<p><b>IP registrada:</b> {lic.ip_registered}</p>"
                            f"<p>Para instalar tu bot, entrá por SSH a tu VPS y ejecutá:</p>"
                            f"<p style='font-family:monospace;background:#f0f0f0;padding:8px'>{cmd}</p>"
                            f"<p>Después de instalar, escribí <b>whabot</b> para vincular tu WhatsApp.</p>"
                            f"<p>Entrá a tu panel: <a href='{PUBLIC_URL}/login'>{PUBLIC_URL}/login</a></p>"
                            f"<hr><p><b>¿No tenés un servidor (VPS)?</b><br>"
                            f"Te recomendamos DatabaseMart (2 núcleos, 3.8GB RAM, ~US$4/mes): "
                            f"<a href='https://www.vps-mart.com/?aff_id=37c805dcd6b340f19b1e4d3f9921fdaf'>vps-mart.com</a><br>"
                            f"O InterServer (2GB RAM, ~US$3/mes): "
                            f"<a href='https://www.interserver.net/r/976162'>interserver.net</a><br>"
                            f"Si no sabés instalarlo, lo dejamos funcionando por un costo extra. Escribinos.</p>")
                    send_email(cli.email, "Tu acceso a WhaConnect está listo", html)
            except Exception as e:
                print("Error enviando email post-pago:", e)
    return {"ok": True}

@app.get("/api/license/{lic_id}")
def get_license(lic_id: int, db: Session = Depends(get_db)):
    lic = db.query(models.License).filter(models.License.id == lic_id).first()
    if not lic:
        raise HTTPException(404, "No existe")
    return {"status": lic.status, "token": lic.token if lic.status == "active" else None,
            "install_cmd": f"bash <(curl -s {PUBLIC_URL}/install/{lic.token})" if lic.status == "active" else None}

import smtplib, ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import timedelta
from jose import jwt

SMTP_HOST = os.getenv("SMTP_HOST"); SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_USER = os.getenv("SMTP_USER"); SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL"); SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", "WhaConnect")
JWT_SECRET = os.getenv("JWT_SECRET", _secrets.token_hex(32))

def send_email(to, subject, html):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{SMTP_FROM_NAME} <{SMTP_FROM_EMAIL}>"
    msg["To"] = to
    msg.attach(MIMEText(html, "html"))
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx) as s:
        s.login(SMTP_USER, SMTP_PASSWORD)
        s.sendmail(SMTP_FROM_EMAIL, to, msg.as_string())

class LoginReq(BaseModel):
    email: str

@app.post("/api/login")
def login(req: LoginReq, db: Session = Depends(get_db)):
    cli = db.query(models.Client).filter(models.Client.email == req.email).first()
    if not cli:
        return {"ok": True}  # no revelamos si existe o no
    tok = _secrets.token_urlsafe(32)
    ml = models.MagicLink(client_id=cli.id, token=tok,
                          expires_at=datetime.utcnow() + timedelta(minutes=20))
    db.add(ml); db.commit()
    link = f"{PUBLIC_URL}/api/verify/{tok}"
    send_email(req.email, "Tu acceso a WhaConnect",
               f'<p>Hacé clic para entrar a tu panel:</p><p><a href="{link}">Acceder a WhaConnect</a></p><p>El enlace vence en 20 minutos.</p>')
    return {"ok": True}

@app.get("/api/verify/{token}")
def verify(token: str, db: Session = Depends(get_db)):
    ml = db.query(models.MagicLink).filter(models.MagicLink.token == token).first()
    if not ml or ml.used or ml.expires_at < datetime.utcnow():
        from fastapi.responses import RedirectResponse as _RR
        return _RR(url="/login?expired=1")
    ml.used = True; db.commit()
    session = jwt.encode({"client_id": ml.client_id,
                          "exp": datetime.utcnow() + timedelta(days=7)}, JWT_SECRET, algorithm="HS256")
    from fastapi.responses import RedirectResponse
    resp = RedirectResponse(url="/client/dashboard")
    resp.set_cookie("wha_session", session, httponly=True, secure=True, samesite="lax", max_age=7*24*3600)
    return resp

def current_client(request: Request, db: Session = Depends(get_db)):
    tok = request.cookies.get("wha_session")
    if not tok:
        raise HTTPException(401, "No autenticado")
    try:
        payload = jwt.decode(tok, JWT_SECRET, algorithms=["HS256"])
    except Exception:
        raise HTTPException(401, "Sesion invalida")
    cli = db.query(models.Client).filter(models.Client.id == payload["client_id"]).first()
    if not cli:
        raise HTTPException(401, "Cliente no existe")
    return cli

# ---- Helper: licencia del cliente logueado ----
def client_license(cli, db):
    lic = db.query(models.License).filter(models.License.client_id == cli.id).first()
    if not lic:
        raise HTTPException(404, "Sin licencia")
    return lic

# ---- Estado del panel ----
@app.get("/api/me")
def me(request: Request, db: Session = Depends(get_db)):
    cli = current_client(request, db)
    lic = db.query(models.License).filter(models.License.client_id == cli.id).first()
    cmds = db.query(models.Command).filter(models.Command.license_id == lic.id).count() if lic else 0
    return {"email": cli.email,
            "license": None if not lic else {
                "status": lic.status, "ip_registered": lic.ip_registered,
                "ip_bound": lic.ip_bound, "ip_changes_used": lic.ip_changes_used,
                "can_change_ip": lic.ip_changes_used < 1, "commands": cmds}}

# ---- Comandos ----
class CmdReq(BaseModel):
    trigger: str
    type: str = "response"   # response | action
    content: str = ""
    active: bool = True
    image_file: str = ""

@app.get("/api/commands")
def list_commands(request: Request, db: Session = Depends(get_db)):
    cli = current_client(request, db); lic = client_license(cli, db)
    cmds = db.query(models.Command).filter(models.Command.license_id == lic.id).all()
    return [{"id": c.id, "trigger": c.trigger, "type": c.type,
             "content": c.content, "active": c.active,
             "image_file": c.image_file or ""} for c in cmds]

@app.post("/api/commands")
def create_command(req: CmdReq, request: Request, db: Session = Depends(get_db)):
    cli = current_client(request, db); lic = client_license(cli, db)
    c = models.Command(license_id=lic.id, trigger=req.trigger, type=req.type,
                       content=req.content, active=req.active,
                       image_file=req.image_file or None)
    db.add(c); db.commit(); db.refresh(c)
    return {"id": c.id}

@app.put("/api/commands/{cid}")
def update_command(cid: int, req: CmdReq, request: Request, db: Session = Depends(get_db)):
    cli = current_client(request, db); lic = client_license(cli, db)
    c = db.query(models.Command).filter(models.Command.id == cid,
                                        models.Command.license_id == lic.id).first()
    if not c: raise HTTPException(404, "No existe")
    c.trigger = req.trigger; c.type = req.type; c.content = req.content; c.active = req.active
    c.image_file = req.image_file or None
    db.commit(); return {"ok": True}

@app.delete("/api/commands/{cid}")
def delete_command(cid: int, request: Request, db: Session = Depends(get_db)):
    cli = current_client(request, db); lic = client_license(cli, db)
    c = db.query(models.Command).filter(models.Command.id == cid,
                                        models.Command.license_id == lic.id).first()
    if not c: raise HTTPException(404, "No existe")
    db.delete(c); db.commit(); return {"ok": True}

# ---- Cambio de IP (1 gratis) ----
class IpReq(BaseModel):
    new_ip: str

@app.post("/api/change-ip")
def change_ip(req: IpReq, request: Request, db: Session = Depends(get_db)):
    cli = current_client(request, db); lic = client_license(cli, db)
    if lic.ip_changes_used >= 1:
        raise HTTPException(403, "Ya usaste tu cambio de IP gratis")
    lic.ip_registered = req.new_ip
    lic.ip_bound = None  # se vuelve a fijar en la proxima instalacion
    lic.ip_changes_used += 1
    db.commit()
    return {"ok": True, "new_ip": req.new_ip, "changes_left": 1 - lic.ip_changes_used}

# ---- Sync: el bot baja sus comandos (valida token + IP) ----
@app.get("/api/bot/commands/{token}")
def bot_commands(token: str, request: Request, db: Session = Depends(get_db)):
    lic = db.query(models.License).filter(models.License.token == token).first()
    if not lic or lic.status != "active":
        raise HTTPException(403, "Licencia invalida")
    ip = client_ip(request)
    if lic.ip_bound and ip != lic.ip_bound:
        raise HTTPException(403, "IP no autorizada")
    lic.last_seen = datetime.utcnow()
    db.commit()
    cmds = db.query(models.Command).filter(models.Command.license_id == lic.id,
                                           models.Command.active == True).all()
    return [{"trigger": c.trigger, "type": c.type, "content": c.content,
             "image_file": c.image_file or ""} for c in cmds]

# ===================== ADMIN =====================
from fastapi import Response

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
ADMIN_SECRET = os.getenv("ADMIN_SECRET", _secrets.token_hex(32))

class AdminLogin(BaseModel):
    password: str

@app.post("/api/admin/login")
def admin_login(req: AdminLogin):
    if not ADMIN_PASSWORD or req.password != ADMIN_PASSWORD:
        raise HTTPException(401, "Clave incorrecta")
    tok = jwt.encode({"admin": True, "exp": datetime.utcnow() + timedelta(days=1)},
                     ADMIN_SECRET, algorithm="HS256")
    from fastapi.responses import JSONResponse
    resp = JSONResponse({"ok": True})
    resp.set_cookie("wha_admin", tok, httponly=True, secure=True, samesite="lax", max_age=24*3600)
    return resp

def require_admin(request: Request):
    tok = request.cookies.get("wha_admin")
    if not tok:
        raise HTTPException(401, "No autorizado")
    try:
        jwt.decode(tok, ADMIN_SECRET, algorithms=["HS256"])
    except Exception:
        raise HTTPException(401, "Sesion admin invalida")

@app.get("/api/admin/metrics")
def admin_metrics(request: Request, db: Session = Depends(get_db)):
    require_admin(request)
    total_clients = db.query(models.Client).count()
    active = db.query(models.License).filter(models.License.status == "active").count()
    pending = db.query(models.License).filter(models.License.status == "pending").count()
    revoked = db.query(models.License).filter(models.License.status == "revoked").count()
    from sqlalchemy import func
    revenue = db.query(func.coalesce(func.sum(models.Payment.amount), 0)).filter(
        models.Payment.status == "approved").scalar()
    return {"clients": total_clients, "active": active, "pending": pending,
            "revoked": revoked, "revenue": int(revenue), "sales": active}

@app.get("/api/admin/clients")
def admin_clients(request: Request, db: Session = Depends(get_db)):
    require_admin(request)
    return [{"id": c.id, "email": c.email,
             "created_at": c.created_at.isoformat() if c.created_at else None}
            for c in db.query(models.Client).order_by(models.Client.id.desc()).all()]

@app.get("/api/admin/licenses")
def admin_licenses(request: Request, db: Session = Depends(get_db)):
    require_admin(request)
    out = []
    for l in db.query(models.License).order_by(models.License.id.desc()).all():
        cli = db.query(models.Client).filter(models.Client.id == l.client_id).first()
        out.append({"id": l.id, "token": l.token, "status": l.status,
                    "email": cli.email if cli else None,
                    "ip_registered": l.ip_registered, "ip_bound": l.ip_bound,
                    "ip_changes_used": l.ip_changes_used,
                    "ai_enabled": bool(l.ai_enabled),
                    "voice_enabled": bool(getattr(l,"voice_enabled",False))})
    return out

class AdminIpReq(BaseModel):
    new_ip: str

@app.post("/api/admin/licenses/{lid}/revoke")
def admin_revoke(lid: int, request: Request, db: Session = Depends(get_db)):
    require_admin(request)
    l = db.query(models.License).filter(models.License.id == lid).first()
    if not l: raise HTTPException(404, "No existe")
    l.status = "revoked"; db.commit(); return {"ok": True}

@app.post("/api/admin/licenses/{lid}/activate")
def admin_activate(lid: int, request: Request, db: Session = Depends(get_db)):
    require_admin(request)
    l = db.query(models.License).filter(models.License.id == lid).first()
    if not l: raise HTTPException(404, "No existe")
    l.status = "active"; db.commit(); return {"ok": True}

@app.post("/api/admin/licenses/{lid}/change-ip")
def admin_change_ip(lid: int, req: AdminIpReq, request: Request, db: Session = Depends(get_db)):
    require_admin(request)
    l = db.query(models.License).filter(models.License.id == lid).first()
    if not l: raise HTTPException(404, "No existe")
    l.ip_registered = req.new_ip; l.ip_bound = None; db.commit()
    return {"ok": True}

@app.get("/api/admin/payments")
def admin_payments(request: Request, db: Session = Depends(get_db)):
    require_admin(request)
    return [{"id": p.id, "mp_payment_id": p.mp_payment_id, "amount": p.amount,
             "status": p.status, "license_id": p.license_id,
             "created_at": p.created_at.isoformat() if p.created_at else None}
            for p in db.query(models.Payment).order_by(models.Payment.id.desc()).all()]

# ===================== ARCHIVOS =====================
import shutil
from fastapi import UploadFile, File
from fastapi.responses import FileResponse

UPLOADS_DIR = "/opt/whaconnect/uploads"
MAX_FILE_SIZE = 16 * 1024 * 1024  # 16 MB
os.makedirs(UPLOADS_DIR, exist_ok=True)

def license_dir(license_id):
    d = os.path.join(UPLOADS_DIR, str(license_id))
    os.makedirs(d, exist_ok=True)
    return d

# ---- Subir archivo (desde el panel del cliente) ----
@app.post("/api/files")
async def upload_file(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)):
    cli = current_client(request, db)
    lic = client_license(cli, db)
    # leer respetando el limite
    contents = await file.read()
    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(413, "Archivo demasiado grande (máx 16 MB)")
    # sanitizar nombre
    safe = os.path.basename(file.filename).replace("/", "_").replace("\\", "_")
    dest = os.path.join(license_dir(lic.id), safe)
    with open(dest, "wb") as f:
        f.write(contents)
    # registrar (o actualizar si ya existia)
    bf = db.query(models.BotFile).filter(models.BotFile.license_id == lic.id,
                                         models.BotFile.filename == safe).first()
    if not bf:
        bf = models.BotFile(license_id=lic.id, filename=safe, size=len(contents))
        db.add(bf)
    else:
        bf.size = len(contents)
    db.commit()
    return {"ok": True, "filename": safe, "size": len(contents)}

# ---- Listar archivos (panel) ----
@app.get("/api/files")
def list_files(request: Request, db: Session = Depends(get_db)):
    cli = current_client(request, db)
    lic = client_license(cli, db)
    files = db.query(models.BotFile).filter(models.BotFile.license_id == lic.id).all()
    return [{"id": f.id, "filename": f.filename, "size": f.size,
             "created_at": f.created_at.isoformat() if f.created_at else None} for f in files]

# ---- Eliminar archivo (panel) ----
@app.delete("/api/files/{fid}")
def delete_file(fid: int, request: Request, db: Session = Depends(get_db)):
    cli = current_client(request, db)
    lic = client_license(cli, db)
    bf = db.query(models.BotFile).filter(models.BotFile.id == fid,
                                         models.BotFile.license_id == lic.id).first()
    if not bf:
        raise HTTPException(404, "No existe")
    fp = os.path.join(license_dir(lic.id), bf.filename)
    if os.path.exists(fp):
        os.remove(fp)
    db.delete(bf); db.commit()
    return {"ok": True}

# ---- El bot lista sus archivos (token + IP) ----
@app.get("/api/bot/files/{token}")
def bot_list_files(token: str, request: Request, db: Session = Depends(get_db)):
    lic = db.query(models.License).filter(models.License.token == token).first()
    if not lic or lic.status != "active":
        raise HTTPException(403, "Licencia invalida")
    ip = client_ip(request)
    if lic.ip_bound and ip != lic.ip_bound:
        raise HTTPException(403, "IP no autorizada")
    files = db.query(models.BotFile).filter(models.BotFile.license_id == lic.id).all()
    return [{"id": f.id, "filename": f.filename, "size": f.size} for f in files]

# ---- El bot descarga un archivo suyo (token + IP) ----
@app.get("/api/bot/files/{token}/{fid}")
def bot_download_file(token: str, fid: int, request: Request, db: Session = Depends(get_db)):
    lic = db.query(models.License).filter(models.License.token == token).first()
    if not lic or lic.status != "active":
        raise HTTPException(403, "Licencia invalida")
    ip = client_ip(request)
    if lic.ip_bound and ip != lic.ip_bound:
        raise HTTPException(403, "IP no autorizada")
    bf = db.query(models.BotFile).filter(models.BotFile.id == fid,
                                         models.BotFile.license_id == lic.id).first()
    if not bf:
        raise HTTPException(404, "No existe")
    fp = os.path.join(license_dir(lic.id), bf.filename)
    if not os.path.exists(fp):
        raise HTTPException(404, "Archivo no encontrado en disco")
    return FileResponse(fp, filename=bf.filename)

# ===================== SETTINGS (acciones automáticas) =====================
class SettingsReq(BaseModel):
    welcome_on: bool = False
    welcome_text: str = "¡Bienvenido {usuario} a {grupo}! 🎉"
    welcome_photo: bool = True
    farewell_on: bool = False
    farewell_text: str = "👋 {usuario} salió de {grupo}"
    antilink_on: bool = False
    antilink_action: str = "borrar"
    antispam_on: bool = False
    antispam_max: int = 5
    antispam_seconds: int = 10
    antispam_action: str = "borrar"

def get_or_create_settings(lic_id, db):
    s = db.query(models.Settings).filter(models.Settings.license_id == lic_id).first()
    if not s:
        s = models.Settings(license_id=lic_id)
        db.add(s); db.commit(); db.refresh(s)
    return s

@app.get("/api/settings")
def read_settings(request: Request, db: Session = Depends(get_db)):
    cli = current_client(request, db); lic = client_license(cli, db)
    s = get_or_create_settings(lic.id, db)
    return {"welcome_on": s.welcome_on, "welcome_text": s.welcome_text,
            "welcome_photo": s.welcome_photo, "farewell_on": s.farewell_on,
            "farewell_text": s.farewell_text,
            "antilink_on": s.antilink_on, "antilink_action": s.antilink_action,
            "antispam_on": s.antispam_on, "antispam_max": s.antispam_max,
            "antispam_seconds": s.antispam_seconds, "antispam_action": s.antispam_action}

@app.put("/api/settings")
def save_settings(req: SettingsReq, request: Request, db: Session = Depends(get_db)):
    cli = current_client(request, db); lic = client_license(cli, db)
    s = get_or_create_settings(lic.id, db)
    s.welcome_on = req.welcome_on; s.welcome_text = req.welcome_text
    s.welcome_photo = req.welcome_photo; s.farewell_on = req.farewell_on
    s.farewell_text = req.farewell_text
    s.antilink_on = req.antilink_on; s.antilink_action = req.antilink_action
    s.antispam_on = req.antispam_on; s.antispam_max = req.antispam_max
    s.antispam_seconds = req.antispam_seconds; s.antispam_action = req.antispam_action
    db.commit()
    return {"ok": True}

# El bot baja la config (token + IP)
@app.get("/api/bot/settings/{token}")
def bot_settings(token: str, request: Request, db: Session = Depends(get_db)):
    lic = db.query(models.License).filter(models.License.token == token).first()
    if not lic or lic.status != "active":
        raise HTTPException(403, "Licencia invalida")
    ip = client_ip(request)
    if lic.ip_bound and ip != lic.ip_bound:
        raise HTTPException(403, "IP no autorizada")
    s = get_or_create_settings(lic.id, db)
    return {"welcome_on": s.welcome_on, "welcome_text": s.welcome_text,
            "welcome_photo": s.welcome_photo, "farewell_on": s.farewell_on,
            "farewell_text": s.farewell_text,
            "antilink_on": s.antilink_on, "antilink_action": s.antilink_action,
            "antispam_on": s.antispam_on, "antispam_max": s.antispam_max,
            "antispam_seconds": s.antispam_seconds, "antispam_action": s.antispam_action}

# ===================== IA (DeepSeek) =====================
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

class AIGenReq(BaseModel):
    descripcion: str

class AIImproveReq(BaseModel):
    texto: str

def call_deepseek(system, user):
    import requests as _rq
    if not DEEPSEEK_API_KEY:
        raise HTTPException(503, "IA no configurada")
    r = _rq.post("https://api.deepseek.com/chat/completions",
        headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
        json={"model": "deepseek-v4-flash",
              "messages": [{"role": "system", "content": system},
                           {"role": "user", "content": user}],
              "temperature": 0.7, "max_tokens": 500},
        timeout=40)
    if r.status_code != 200:
        raise HTTPException(502, f"Error de IA: {r.status_code}")
    return r.json()["choices"][0]["message"]["content"].strip()

@app.post("/api/ai/generate")
def ai_generate(req: AIGenReq, request: Request, db: Session = Depends(get_db)):
    cli = current_client(request, db); client_license(cli, db)
    system = ("Sos un asistente que crea comandos para un bot de WhatsApp. "
              "El usuario describe qué quiere. Respondé SOLO con un JSON válido, sin texto extra, "
              'con este formato exacto: {"trigger": "/comando", "respuesta": "texto que responde el bot"}. '
              "El trigger debe empezar con / y ser corto. La respuesta puede usar emojis y saltos de línea.")
    out = call_deepseek(system, req.descripcion)
    import json as _json, re as _re
    out = _re.sub(r"```json|```", "", out).strip()
    try:
        data = _json.loads(out)
        return {"trigger": data.get("trigger", ""), "respuesta": data.get("respuesta", "")}
    except Exception:
        return {"trigger": "", "respuesta": out}

@app.post("/api/ai/improve")
def ai_improve(req: AIImproveReq, request: Request, db: Session = Depends(get_db)):
    cli = current_client(request, db); client_license(cli, db)
    system = ("Mejorá y reescribí el siguiente texto para que sea claro, amable y profesional, "
              "apto para un mensaje de WhatsApp. Mantené el idioma original. "
              "Respondé SOLO con el texto mejorado, sin comillas ni explicaciones.")
    out = call_deepseek(system, req.texto)
    return {"texto": out}

# ---- Admin: crear licencia manual (sin pago) ----
class AdminNewLicense(BaseModel):
    email: str
    ip: str

@app.post("/api/admin/licenses/create")
def admin_create_license(req: AdminNewLicense, request: Request, db: Session = Depends(get_db)):
    require_admin(request)
    cli = db.query(models.Client).filter(models.Client.email == req.email).first()
    if not cli:
        cli = models.Client(email=req.email)
        db.add(cli); db.commit(); db.refresh(cli)
    token = _secrets.token_hex(16)
    lic = models.License(token=token, client_id=cli.id, status="active",
                         ip_registered=req.ip, ip_changes_used=0)
    db.add(lic); db.commit(); db.refresh(lic)
    return {"ok": True, "token": token, "license_id": lic.id,
            "install_cmd": f"bash <(curl -s {PUBLIC_URL}/install/{token})"}

# ---- IA: generar texto de bienvenida/despedida (respeta placeholders) ----
class AIWelcomeReq(BaseModel):
    descripcion: str

@app.post("/api/ai/welcome")
def ai_welcome(req: AIWelcomeReq, request: Request, db: Session = Depends(get_db)):
    cli = current_client(request, db); client_license(cli, db)
    system = ("Generás un mensaje corto y cálido para un grupo de WhatsApp (bienvenida o despedida). "
              "OBLIGATORIO: incluí siempre el placeholder {usuario} para mencionar a la persona y "
              "{grupo} para el nombre del grupo, escritos EXACTAMENTE así con las llaves. "
              "Podés usar emojis. Respondé SOLO con el texto, sin comillas ni explicaciones.")
    out = call_deepseek(system, req.descripcion)
    # garantizar que los placeholders estén presentes
    if "{usuario}" not in out:
        out = "{usuario} " + out
    if "{grupo}" not in out:
        out = out + " en {grupo}"
    return {"texto": out}

# ===================== LOGIN CON CONTRASEÑA =====================
from passlib.context import CryptContext
pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

class PwLoginReq(BaseModel):
    email: str
    password: str

class SetPwReq(BaseModel):
    email: str
    password: str

class ResetReq(BaseModel):
    email: str

class ResetConfirmReq(BaseModel):
    email: str
    code: str
    new_password: str

def make_session(client_id):
    return jwt.encode({"client_id": client_id,
                       "exp": datetime.utcnow() + timedelta(days=7)}, JWT_SECRET, algorithm="HS256")

# Login con email + contraseña (solo si tiene licencia activa)
@app.post("/api/login-password")
def login_password(req: PwLoginReq, db: Session = Depends(get_db)):
    cli = db.query(models.Client).filter(models.Client.email == req.email).first()
    if not cli or not cli.password_hash or not pwd_ctx.verify(req.password, cli.password_hash):
        raise HTTPException(401, "Email o contraseña incorrectos")
    activa = db.query(models.License).filter(models.License.client_id == cli.id,
                                             models.License.status == "active").first()
    if not activa:
        raise HTTPException(403, "Necesitás una compra activa para ingresar al panel")
    from fastapi.responses import JSONResponse
    resp = JSONResponse({"ok": True})
    resp.set_cookie("wha_session", make_session(cli.id), httponly=True, secure=True,
                    samesite="lax", max_age=7*24*3600)
    return resp

# Recuperar: enviar código de 6 dígitos
@app.post("/api/reset-request")
def reset_request(req: ResetReq, db: Session = Depends(get_db)):
    cli = db.query(models.Client).filter(models.Client.email == req.email).first()
    if cli:
        import random
        code = f"{random.randint(0, 999999):06d}"
        rc = models.ResetCode(client_id=cli.id, code=code,
                              expires_at=datetime.utcnow() + timedelta(minutes=15))
        db.add(rc); db.commit()
        try:
            send_email(req.email, "Código de recuperación - WhaConnect",
                       f"<p>Tu código para restablecer la contraseña es:</p>"
                       f"<h2 style='letter-spacing:4px'>{code}</h2>"
                       f"<p>Vence en 15 minutos.</p>")
        except Exception as e:
            print("Error enviando código:", e)
    return {"ok": True}

# Recuperar: confirmar código y definir nueva contraseña
@app.post("/api/reset-confirm")
def reset_confirm(req: ResetConfirmReq, db: Session = Depends(get_db)):
    cli = db.query(models.Client).filter(models.Client.email == req.email).first()
    if not cli:
        raise HTTPException(400, "Datos inválidos")
    rc = db.query(models.ResetCode).filter(models.ResetCode.client_id == cli.id,
                                           models.ResetCode.code == req.code,
                                           models.ResetCode.used == False).first()
    if not rc or rc.expires_at < datetime.utcnow():
        raise HTTPException(400, "Código inválido o vencido")
    if len(req.new_password) < 6:
        raise HTTPException(400, "La contraseña debe tener al menos 6 caracteres")
    cli.password_hash = pwd_ctx.hash(req.new_password)
    rc.used = True; db.commit()
    return {"ok": True}


@app.get("/api/bot-status")
def bot_status(request: Request, db: Session = Depends(get_db)):
    cli = current_client(request, db)
    lic = db.query(models.License).filter(models.License.client_id == cli.id).first()
    if not lic:
        raise HTTPException(404, "Sin licencia")
    online = False
    last = None
    if lic.last_seen:
        delta = (datetime.utcnow() - lic.last_seen).total_seconds()
        online = delta < 180  # visto hace menos de 3 min
        last = lic.last_seen.isoformat()
    cmds = db.query(models.Command).filter(models.Command.license_id == lic.id,
                                           models.Command.active == True).count()
    necesita_update = bool(lic.bot_version) and lic.bot_version != BOT_VERSION_ACTUAL
    fix_cmd = f"bash <(curl -s {PUBLIC_URL}/install/{lic.token}) && whabot stop && whabot fondo"
    return {"online": online, "last_seen": last, "commands": cmds,
            "bot_version": lic.bot_version or "desconocida",
            "version_actual": BOT_VERSION_ACTUAL,
            "necesita_update": necesita_update,
            "update_cmd": f"bash <(curl -s {PUBLIC_URL}/install/{lic.token})" if necesita_update else None,
            "fix_cmd": fix_cmd}

# ---- Admin: estado de recursos del servidor ----
@app.get("/api/admin/system")
def admin_system(request: Request, db: Session = Depends(get_db)):
    require_admin(request)
    import shutil, os
    # CPU y RAM con /proc (sin dependencias externas)
    def cpu_pct():
        try:
            with open("/proc/loadavg") as f:
                load = float(f.read().split()[0])
            ncpu = os.cpu_count() or 1
            return round(min(load / ncpu * 100, 100), 1)
        except: return 0
    def ram():
        try:
            info = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    k, v = line.split(":")
                    info[k.strip()] = int(v.strip().split()[0])
            total = info.get("MemTotal", 0) / 1024
            avail = info.get("MemAvailable", 0) / 1024
            used = total - avail
            return round(used), round(total), round(used/total*100, 1) if total else 0
        except: return 0, 0, 0
    # Disco
    du = shutil.disk_usage("/")
    disk_used = du.used // (1024**2)
    disk_total = du.total // (1024**2)
    disk_pct = round(du.used / du.total * 100, 1)
    # Tamaño de uploads
    up_size = 0
    for root, dirs, files in os.walk(UPLOADS_DIR):
        for f in files:
            try: up_size += os.path.getsize(os.path.join(root, f))
            except: pass
    ram_used, ram_total, ram_pct = ram()
    return {
        "cpu_pct": cpu_pct(),
        "ram_used_mb": ram_used, "ram_total_mb": ram_total, "ram_pct": ram_pct,
        "disk_used_mb": disk_used, "disk_total_mb": disk_total, "disk_pct": disk_pct,
        "uploads_mb": round(up_size / (1024**2), 1)
    }

# ===================== ARCHIVOS DE COMANDOS =====================
CMD_FILES_DIR = "/opt/whaconnect/cmd_files"
os.makedirs(CMD_FILES_DIR, exist_ok=True)

def cmd_files_dir(license_id):
    d = os.path.join(CMD_FILES_DIR, str(license_id))
    os.makedirs(d, exist_ok=True)
    return d

# Subir un archivo para usar en comandos
@app.post("/api/command-files/upload")
async def upload_command_file(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)):
    cli = current_client(request, db)
    lic = client_license(cli, db)
    contents = await file.read()
    if len(contents) > 16 * 1024 * 1024:
        raise HTTPException(400, "El archivo supera los 16MB")
    safe = "".join(c for c in file.filename if c.isalnum() or c in "._- ").strip()
    if not safe:
        safe = "archivo"
    d = cmd_files_dir(lic.id)
    with open(os.path.join(d, safe), "wb") as f:
        f.write(contents)
    bf = db.query(models.CommandFile).filter(models.CommandFile.license_id == lic.id,
                                             models.CommandFile.filename == safe).first()
    if not bf:
        bf = models.CommandFile(license_id=lic.id, filename=safe, size=len(contents))
        db.add(bf); db.commit()
    return {"ok": True, "filename": safe}

# Listar archivos de comandos (para elegir en el panel)
@app.get("/api/command-files")
def list_command_files(request: Request, db: Session = Depends(get_db)):
    cli = current_client(request, db)
    lic = client_license(cli, db)
    files = db.query(models.CommandFile).filter(models.CommandFile.license_id == lic.id).all()
    return [{"id": f.id, "filename": f.filename, "size": f.size} for f in files]

# El bot lista los archivos de comandos para descargarlos
@app.get("/api/bot/command-files/{token}")
def bot_command_files(token: str, db: Session = Depends(get_db)):
    lic = db.query(models.License).filter(models.License.token == token).first()
    if not lic or lic.status != "active":
        raise HTTPException(403, "Licencia inactiva")
    files = db.query(models.CommandFile).filter(models.CommandFile.license_id == lic.id).all()
    return [{"filename": f.filename, "size": f.size} for f in files]

# El bot descarga un archivo de comando
@app.get("/api/bot/command-file/{token}/{filename}")
def bot_download_command_file(token: str, filename: str, db: Session = Depends(get_db)):
    from fastapi.responses import FileResponse
    lic = db.query(models.License).filter(models.License.token == token).first()
    if not lic or lic.status != "active":
        raise HTTPException(403, "Licencia inactiva")
    safe = "".join(c for c in filename if c.isalnum() or c in "._- ").strip()
    fp = os.path.join(cmd_files_dir(lic.id), safe)
    if not os.path.exists(fp):
        raise HTTPException(404, "No existe")
    return FileResponse(fp, filename=safe)

# ===================== IA DEL BOT (asistente) =====================
class AIKnowledgeReq(BaseModel):
    knowledge: str

# Cliente: ver su config de IA
@app.get("/api/ai-bot")
def get_ai_bot(request: Request, db: Session = Depends(get_db)):
    cli = current_client(request, db)
    lic = client_license(cli, db)
    return {"enabled": bool(lic.ai_enabled), "knowledge": lic.ai_knowledge or ""}

# Cliente: guardar su info de conocimiento
@app.put("/api/ai-bot")
def save_ai_bot(req: AIKnowledgeReq, request: Request, db: Session = Depends(get_db)):
    cli = current_client(request, db)
    lic = client_license(cli, db)
    lic.ai_knowledge = req.knowledge[:8000]  # límite de tamaño
    db.commit()
    return {"ok": True}

# Admin: activar/desactivar IA de una licencia
@app.post("/api/admin/licenses/{lic_id}/ai")
def admin_toggle_ai(lic_id: int, request: Request, db: Session = Depends(get_db)):
    require_admin(request)
    lic = db.query(models.License).filter(models.License.id == lic_id).first()
    if not lic:
        raise HTTPException(404, "Licencia inexistente")
    lic.ai_enabled = not bool(lic.ai_enabled)
    db.commit()
    return {"ok": True, "ai_enabled": lic.ai_enabled}

# El bot consulta a la IA con la pregunta del usuario
class BotAIReq(BaseModel):
    pregunta: str

@app.post("/api/bot/ai/{token}")
def bot_ai(token: str, req: BotAIReq, db: Session = Depends(get_db)):
    lic = db.query(models.License).filter(models.License.token == token).first()
    if not lic or lic.status != "active":
        raise HTTPException(403, "Licencia inactiva")
    if not lic.ai_enabled:
        raise HTTPException(403, "IA no activada")
    conocimiento = lic.ai_knowledge or "No hay información cargada."
    # Agregar los comandos del cliente como parte del conocimiento de la IA
    cmds = db.query(models.Command).filter(models.Command.license_id == lic.id,
                                           models.Command.active == True).all()
    if cmds:
        lista_cmds = "\n".join([f"- Comando {c.trigger}: {c.content}" for c in cmds if c.type == "response" and c.content])
        if lista_cmds:
            conocimiento = conocimiento + "\n\n=== COMANDOS DISPONIBLES DEL BOT ===\n" + \
                "Estos son los comandos que el usuario puede escribir y su información:\n" + lista_cmds
    system = (
        "Sos un asistente de SOPORTE para un negocio, respondiendo por WhatsApp. "
        "Respondé SOLO con la información proporcionada abajo. Si no sabés algo o te preguntan "
        "algo que no está en la información, decí amablemente que para eso escriban al soporte. "
        "NO inventes precios ni datos. NO cierres ventas por chat: si alguien quiere comprar, "
        "indicale que lo haga desde la página web oficial del negocio. "
        "Sé breve, amable y claro. Respondé en el mismo idioma del usuario.\n\n"
        "=== INFORMACIÓN DEL NEGOCIO ===\n" + conocimiento
    )
    try:
        respuesta = call_deepseek(system, req.pregunta)
        audio_b64 = None
        if getattr(lic, "voice_enabled", False) and tts is not None:
            try:
                audio_b64 = tts.generar_audio_ogg(respuesta, lic.voice_name or "femenina_profesional")
            except Exception as e:
                print("Error generando voz:", e)
        return {"respuesta": respuesta, "audio": audio_b64}
    except Exception as e:
        raise HTTPException(502, "Error de IA")

# ===================== VOZ (TTS) =====================
class VoiceConfigReq(BaseModel):
    voice_name: str

# Cliente: ver config de voz + voces disponibles
@app.get("/api/voice")
def get_voice(request: Request, db: Session = Depends(get_db)):
    cli = current_client(request, db)
    lic = client_license(cli, db)
    voces = ["masculina_dinamica","masculina_profesional","masculina_fuerte",
             "femenina_profesional","femenina_calida","femenina_suave"]
    return {"enabled": bool(getattr(lic,"voice_enabled",False)),
            "voice_name": lic.voice_name or "femenina_profesional",
            "voces_disponibles": voces}

# Cliente: guardar la voz elegida
@app.put("/api/voice")
def save_voice(req: VoiceConfigReq, request: Request, db: Session = Depends(get_db)):
    cli = current_client(request, db)
    lic = client_license(cli, db)
    lic.voice_name = req.voice_name
    db.commit()
    return {"ok": True, "voice_name": lic.voice_name}

# Admin: activar/desactivar voz de una licencia
@app.post("/api/admin/licenses/{lic_id}/voice")
def admin_toggle_voice(lic_id: int, request: Request, db: Session = Depends(get_db)):
    require_admin(request)
    lic = db.query(models.License).filter(models.License.id == lic_id).first()
    if not lic:
        raise HTTPException(404, "Licencia inexistente")
    lic.voice_enabled = not bool(getattr(lic,"voice_enabled",False))
    db.commit()
    return {"ok": True, "voice_enabled": lic.voice_enabled}

# Muestra de voz (genera audio de ejemplo para que el cliente escuche)
@app.get("/api/voice/sample/{voice_key}")
def voice_sample(voice_key: str, request: Request, db: Session = Depends(get_db)):
    cli = current_client(request, db)
    if tts is None:
        raise HTTPException(503, "TTS no disponible")
    audio = tts.generar_audio_ogg("Hola, así sueno yo. Puedo responder las consultas de tus clientes.", voice_key)
    if not audio:
        raise HTTPException(502, "No se pudo generar la muestra")
    return {"audio": audio}

# ===================== PAGOS CON UALÁ BIS =====================
import uala as uala_mod

def _email_postpago(lic, db):
    """Envía el email post-pago (reutilizable para MP y Ualá)."""
    try:
        cli = db.query(models.Client).filter(models.Client.id == lic.client_id).first()
        if not cli:
            return
        cmd = f"bash <(curl -s {PUBLIC_URL}/install/{lic.token})"
        html = (f"<h2>¡Gracias por tu compra en WhaConnect!</h2>"
                f"<p>Tu acceso ya está activo. Token de licencia:</p>"
                f"<p style='font-family:monospace;background:#f0f0f0;padding:8px'>{lic.token}</p>"
                f"<p><b>IP registrada:</b> {lic.ip_registered}</p>"
                f"<p>Para instalar tu bot, entrá por SSH a tu VPS y ejecutá:</p>"
                f"<p style='font-family:monospace;background:#f0f0f0;padding:8px'>{cmd}</p>"
                f"<p>Después de instalar, escribí <b>whabot</b> para vincular tu WhatsApp.</p>"
                f"<p>Entrá a tu panel: <a href='{PUBLIC_URL}/login'>{PUBLIC_URL}/login</a></p>"
                f"<hr><p><b>¿No tenés un servidor (VPS)?</b><br>"
                f"Te recomendamos DatabaseMart (2 núcleos, 3.8GB RAM, ~US$4/mes): "
                f"<a href='https://www.vps-mart.com/?aff_id=37c805dcd6b340f19b1e4d3f9921fdaf'>vps-mart.com</a><br>"
                f"O InterServer (2GB RAM, ~US$3/mes): "
                f"<a href='https://www.interserver.net/r/976162'>interserver.net</a><br>"
                f"Si no sabés instalarlo, lo dejamos funcionando por un costo extra. Escribinos.</p>")
        send_email(cli.email, "Tu acceso a WhaConnect está listo", html)
    except Exception as e:
        print("Error email post-pago:", e)

@app.post("/api/buy-uala")
def buy_uala(req: BuyReq, db: Session = Depends(get_db)):
    if not req.accept_terms:
        raise HTTPException(400, "Debés aceptar los términos y condiciones")
    if not req.password or len(req.password) < 6:
        raise HTTPException(400, "La contraseña debe tener al menos 6 caracteres")
    cli = db.query(models.Client).filter(models.Client.email == req.email).first()
    if not cli:
        cli = models.Client(email=req.email)
        db.add(cli); db.commit(); db.refresh(cli)
    cli.password_hash = pwd_ctx.hash(req.password)
    db.commit()
    token = _secrets.token_hex(16)
    lic = models.License(token=token, client_id=cli.id, status="pending",
                         ip_registered=req.ip, ip_changes_used=0)
    db.add(lic); db.commit(); db.refresh(lic)
    try:
        link = uala_mod.crear_orden(
            amount=MP_PRICE,
            description="Acceso WhaConnect (pago unico)",
            external_reference=str(lic.id),
            success_url=f"{PUBLIC_URL}/payment-success?lic={lic.id}",
            fail_url=f"{PUBLIC_URL}/buy?error=1",
            notification_url=f"{PUBLIC_URL}/uala/webhook")
    except Exception as e:
        print("Error creando orden Uala:", e)
        raise HTTPException(502, "No se pudo crear el pago con Ualá")
    return {"init_point": link, "license_id": lic.id}

@app.post("/uala/webhook")
async def uala_webhook(request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    status = data.get("status")
    ext_ref = data.get("external_reference")
    if status in ("APPROVED", "PROCESSED") and ext_ref:
        lic = db.query(models.License).filter(models.License.id == int(ext_ref)).first()
        if lic and lic.status != "active":
            lic.status = "active"
            db.add(models.Payment(license_id=lic.id, mp_payment_id=str(data.get("uuid","")),
                                  amount=MP_PRICE, status="approved"))
            db.commit()
            _email_postpago(lic, db)
    return {"ok": True}

# ===================== LEER IMAGEN PARA EL CONOCIMIENTO IA =====================
import vision as vision_mod

@app.post("/api/ai-bot/leer-imagen")
async def leer_imagen_ia(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)):
    cli = current_client(request, db)
    lic = client_license(cli, db)
    contents = await file.read()
    if len(contents) > 10 * 1024 * 1024:
        raise HTTPException(400, "La imagen supera los 10MB")
    mime = file.content_type or "image/jpeg"
    texto = vision_mod.leer_imagen(contents, mime)
    if not texto:
        raise HTTPException(502, "No se pudo leer la imagen")
    # Sumar el texto extraído al conocimiento existente
    actual = lic.ai_knowledge or ""
    nuevo = (actual + "\n\n=== INFORMACIÓN DE IMAGEN ===\n" + texto)[:8000]
    lic.ai_knowledge = nuevo
    db.commit()
    return {"ok": True, "texto_extraido": texto, "conocimiento_actualizado": nuevo}
