# Módulo de lectura de imágenes con Gemini Vision (Vertex AI)
import base64
import google.auth
import google.auth.transport.requests
from google.oauth2 import service_account
import requests

CRED_PATH = "/opt/whaconnect/api/google-tts.json"
PROJECT = "cleanbot-8f137"
LOCATION = "us-central1"
VISION_MODEL = "gemini-2.5-flash"

def _token():
    creds = service_account.Credentials.from_service_account_file(
        CRED_PATH, scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(google.auth.transport.requests.Request())
    return creds.token

def leer_imagen(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
    """Extrae la información de una imagen (catálogo, flyer, etc.) como texto."""
    try:
        token = _token()
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        base_url = f"https://{LOCATION}-aiplatform.googleapis.com/v1/projects/{PROJECT}/locations/{LOCATION}/publishers/google/models"
        prompt = ("Extraé TODA la información de esta imagen en texto claro y ordenado. "
                  "Si es un catálogo o lista de precios, listá cada producto con su precio. "
                  "Si es un flyer o promoción, describí la oferta y condiciones. "
                  "Devolvé solo la información, sin comentarios tuyos. En español.")
        r = requests.post(
            f"{base_url}/{VISION_MODEL}:generateContent",
            json={"contents": [{"role": "user", "parts": [
                {"text": prompt},
                {"inlineData": {"mimeType": mime_type, "data": b64}}
            ]}]},
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=90)
        if r.status_code != 200:
            print(f"Error vision {r.status_code}: {r.text[:200]}")
            return None
        return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        print(f"Error vision: {e}")
        return None
