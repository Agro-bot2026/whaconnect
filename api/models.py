from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base

class Client(Base):
    __tablename__ = "clients"
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    password_hash = Column(String, nullable=True)
    licenses = relationship("License", back_populates="client")

class License(Base):
    __tablename__ = "licenses"
    id = Column(Integer, primary_key=True)
    token = Column(String, unique=True, nullable=False)
    client_id = Column(Integer, ForeignKey("clients.id"))
    status = Column(String, default="pending")   # pending, active, revoked
    ip_registered = Column(String)               # IP que puso al comprar
    ip_bound = Column(String)                    # IP fijada en 1ra instalacion
    ip_changes_used = Column(Integer, default=0)
    last_seen = Column(DateTime, nullable=True)
    bot_version = Column(String, nullable=True)
    ai_enabled = Column(Boolean, default=False)
    ai_knowledge = Column(Text, default='')
    voice_enabled = Column(Boolean, default=False)
    voice_name = Column(String, default='femenina_profesional')
    created_at = Column(DateTime, default=datetime.utcnow)
    client = relationship("Client", back_populates="licenses")
    commands = relationship("Command", back_populates="license")

class Command(Base):
    __tablename__ = "commands"
    id = Column(Integer, primary_key=True)
    license_id = Column(Integer, ForeignKey("licenses.id"))
    trigger = Column(String, nullable=False)
    type = Column(String, default="response")    # response, action
    content = Column(Text)                        # texto/ruta archivo, o nombre de accion
    image_file = Column(String, nullable=True)
    active = Column(Boolean, default=True)
    license = relationship("License", back_populates="commands")

class Payment(Base):
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True)
    license_id = Column(Integer, ForeignKey("licenses.id"))
    mp_payment_id = Column(String)
    amount = Column(Integer)
    status = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)

class MagicLink(Base):
    __tablename__ = "magic_links"
    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("clients.id"))
    token = Column(String, unique=True, nullable=False)
    used = Column(Boolean, default=False)
    expires_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

class BotFile(Base):
    __tablename__ = "bot_files"
    id = Column(Integer, primary_key=True)
    license_id = Column(Integer, ForeignKey("licenses.id"))
    filename = Column(String, nullable=False)
    size = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

class Settings(Base):
    __tablename__ = "settings"
    id = Column(Integer, primary_key=True)
    license_id = Column(Integer, ForeignKey("licenses.id"), unique=True)
    welcome_on = Column(Boolean, default=False)
    welcome_text = Column(Text, default="¡Bienvenido {usuario} a {grupo}! 🎉")
    welcome_photo = Column(Boolean, default=True)
    farewell_on = Column(Boolean, default=False)
    farewell_text = Column(Text, default="👋 {usuario} salió de {grupo}")
    antilink_on = Column(Boolean, default=False)
    antilink_action = Column(String, default="borrar")
    antispam_on = Column(Boolean, default=False)
    antispam_max = Column(Integer, default=5)
    antispam_seconds = Column(Integer, default=10)
    antispam_action = Column(String, default="borrar")


class ResetCode(Base):
    __tablename__ = "reset_codes"
    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("clients.id"))
    code = Column(String, nullable=False)
    used = Column(Boolean, default=False)
    expires_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)


class CommandFile(Base):
    __tablename__ = "command_files"
    id = Column(Integer, primary_key=True)
    license_id = Column(Integer, ForeignKey("licenses.id"))
    filename = Column(String, nullable=False)
    size = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
