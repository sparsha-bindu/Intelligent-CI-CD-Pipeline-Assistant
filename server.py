# server.py
import os
import hmac
import hashlib
import asyncio
from fastapi import FastAPI, Request, Header, HTTPException
from process_event import process_event_async
from dotenv import load_dotenv

load_dotenv()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "devsecret")

app = FastAPI()

@app.get("/health")
async def health():
    return {"ok": True}

async def verify_signature(body: bytes, header_signature: str | None):
    if not header_signature:
        return False
    mac = hmac.new(WEBHOOK_SECRET.encode(), msg=body, digestmod=hashlib.sha256)
    expected = "sha256=" + mac.hexdigest()
    return hmac.compare_digest(expected, header_signature)

@app.post("/webhook")
async def webhook(request: Request, x_hub_signature_256: str | None = Header(None)):
    body = await request.body()
    if x_hub_signature_256:
        ok = await verify_signature(body, x_hub_signature_256)
        if not ok:
            raise HTTPException(status_code=401, detail="invalid signature")

    payload = await request.json()
    event = normalize_event(payload)
    # fire-and-forget background processing
    asyncio.create_task(process_event_async(event))
    return {"received": True}

def normalize_event(payload: dict) -> dict:
    # very small normalization; extend for production
    if "build" in payload:
        build = payload.get("build")
        return {
            "source": "jenkins",
            "status": build.get("status"),
            "url": build.get("full_url") or build.get("url"),
            "logs": fetch_jenkins_console(build),
            "metadata": payload,
        }
    if "workflow_run" in payload:
        return {
            "source": "github",
            "status": payload.get("action"),
            "url": payload.get("workflow_run", {}).get("html_url"),
            "logs": "",
            "metadata": payload,
        }
    return {"source": "unknown", "status": None, "logs": str(payload), "metadata": payload}

def fetch_jenkins_console(build: dict) -> str:
    url = build.get("full_url") or build.get("url")
    if not url:
        return ""
    console_url = url.rstrip("/") + "/consoleText"
    try:
        import requests
        r = requests.get(console_url, timeout=10)
        return r.text
    except Exception:
        return ""
