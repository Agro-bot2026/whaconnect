# Módulo de Text-to-Speech con Gemini-TTS (Google Vertex AI)
import base64, subprocess, tempfile, os
import requests
import google.auth
import google.auth.transport.requests
from google.oauth2 import service_account

CRED_PATH = "/opt/whaconnect/api/google-tts.json"
PROJECT = "cleanbot-8f137"
LOCATION = "us-central1"
TTS_MODEL = "gemini-3.1-flash-tts-preview"

# Voces disponibles para los clientes (nombre descriptivo -> voz real de Gemini)
VOCES = {
    "masculina_dinamica": "Puck",
    "masculina_profesional": "Charon",
    "masculina_fuerte": "Fenrir",
    "femenina_profesional": "Kore",
    "femenina_calida": "Aoede",
    "femenina_suave": "Leda",
}

import re

def limpiar_texto(texto):
    """Quita emojis, símbolos y formato para que el TTS no los lea."""
    if not texto:
        return ""
    # Quitar emojis y pictogramas
    texto = re.sub(r"[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\U00002190-\U000021FF\U00002B00-\U00002BFF\U0000FE00-\U0000FE0F\U00002700-\U000027BF]", "", texto)
    # Quitar símbolos de formato de WhatsApp y otros
    texto = texto.replace("*", "").replace("_", "").replace("~", "").replace("`", "")
    texto = texto.replace("#", "").replace(">", "").replace("|", " ")
    # Quitar caracteres decorativos comunes
    texto = re.sub(r"[━─═►▶♨✨🔥•·▪◆★☆]", "", texto)
    # Colapsar espacios y saltos múltiples
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto

def _token():
    creds = service_account.Credentials.from_service_account_file(
        CRED_PATH, scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(google.auth.transport.requests.Request())
    return creds.token

def generar_audio_ogg(texto: str, voz_key: str = "femenina_profesional") -> str:
    """Genera audio OGG (base64) a partir de texto. Devuelve None si falla."""
    try:
        texto = limpiar_texto(texto)[:8000]
        if not texto:
            return None
        voice_name = VOCES.get(voz_key, "Kore")
        token = _token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        base_url = f"https://{LOCATION}-aiplatform.googleapis.com/v1beta1/projects/{PROJECT}/locations/{LOCATION}/publishers/google/models"
        r = requests.post(
            f"{base_url}/{TTS_MODEL}:generateContent",
            json={
                "contents": [{"role": "user", "parts": [{"text": texto}]}],
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "speechConfig": {
                        "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice_name}}
                    }
                }
            },
            headers=headers, timeout=120
        )
        if r.status_code != 200:
            print(f"Error TTS {r.status_code}: {r.text[:200]}")
            return None
        audio_pcm = base64.b64decode(r.json()["candidates"][0]["content"]["parts"][0]["inlineData"]["data"])
        with tempfile.NamedTemporaryFile(suffix=".pcm", delete=False) as f:
            f.write(audio_pcm); pcm_path = f.name
        ogg_path = pcm_path.replace(".pcm", ".ogg")
        subprocess.run(
            ["ffmpeg", "-f", "s16le", "-ar", "24000", "-ac", "1", "-i", pcm_path,
             "-c:a", "libopus", ogg_path, "-y"],
            capture_output=True, check=True)
        with open(ogg_path, "rb") as f:
            ogg_bytes = f.read()
        os.unlink(pcm_path); os.unlink(ogg_path)
        return base64.b64encode(ogg_bytes).decode("utf-8")
    except Exception as e:
        print(f"Error TTS: {e}")
        return None
