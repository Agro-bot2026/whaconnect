# Módulo de pagos con Ualá Bis (API Cobros Online v2)
import os, requests

AUTH_URL = "https://auth.developers.ar.ua.la/v2/api/auth/token"
CHECKOUT_URL = "https://checkout.developers.ar.ua.la/v2/api/checkout"

UALA_USERNAME = os.getenv("UALA_USERNAME")
UALA_CLIENT_ID = os.getenv("UALA_CLIENT_ID")
UALA_CLIENT_SECRET = os.getenv("UALA_CLIENT_SECRET")

def _get_token():
    r = requests.post(AUTH_URL, json={
        "username": UALA_USERNAME,
        "client_id": UALA_CLIENT_ID,
        "client_secret_id": UALA_CLIENT_SECRET,
        "grant_type": "client_credentials"
    }, headers={"Content-Type": "application/json"}, timeout=20)
    r.raise_for_status()
    return r.json()["access_token"]

def crear_orden(amount, description, external_reference, success_url, fail_url, notification_url):
    """Crea una orden de pago en Ualá y devuelve el checkout_link."""
    token = _get_token()
    r = requests.post(CHECKOUT_URL, json={
        "amount": str(amount),
        "description": description,
        "callback_success": success_url,
        "callback_fail": fail_url,
        "notification_url": notification_url,
        "external_reference": external_reference
    }, headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"}, timeout=30)
    r.raise_for_status()
    data = r.json()
    return data["links"]["checkout_link"]
