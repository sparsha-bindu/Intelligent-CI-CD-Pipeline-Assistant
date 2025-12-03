# server.py
import os
import hmac
import hashlib
import asyncio
import logging
from typing import Optional

from fastapi import FastAPI, Request, Header, HTTPException
from dotenv import load_dotenv

# Local app imports
# process_event_async is the background worker that handles events
from process_event import process_event_async

load_dotenv()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
# Toggle to skip signature verification for local testing (set SKIP_SIGNATURE=true in .env)
SKIP_SIGNATURE = os.getenv("SKIP_SIGNATURE", "false").lower() in ("1", "true", "yes")

# Minimal logging setup
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ci-assistant-server")

app = FastAPI()


@app.get("/health")
async def health():
    return {"ok": True}


def _normalize_header_value(hdr: Optional[str]) -> Optional[str]:
    """Normalize the incoming header by trimming and lowercasing the prefix."""
    if not hdr:
        return None
    hdr = hdr.strip()
    # allow both "sha256=..." and "sha256:..." and also uppercase/lowercase
    if hdr.lower().startswith("sha256=") or hdr.lower().startswith("sha256:"):
        return hdr
    # if user accidentally sent raw hex, normalize to sha256=<hex>
    if all(c in "0123456789abcdefABCDEF" for c in hdr) and len(hdr) >= 64:
        return "sha256=" + hdr
    return hdr


async def verify_signature(body: bytes, header_signature: Optional[str]) -> bool:
    """
    Verify signature. Accepts two header formats:
      - sha256=<hex>
      - sha256:<hex>

    Returns True only if header present and HMAC matches.
    """
    if SKIP_SIGNATURE:
        log.info("SKIP_SIGNATURE enabled — skipping HMAC verification")
        return True

    if not WEBHOOK_SECRET:
        log.warning("WEBHOOK_SECRET not configured — rejecting signed webhook")
        return False

    header_signature = _normalize_header_value(header_signature)
    if not header_signature:
        return False

    # compute expected hmac hex
    mac = hmac.new(WEBHOOK_SECRET.encode(), msg=body, digestmod=hashlib.sha256)
    expected_hex = mac.hexdigest()
    expected_eq = f"sha256={expected_hex}"
    expected_col = f"sha256:{expected_hex}"

    # constant-time compare both possibilities
    try:
        if hmac.compare_digest(expected_eq, header_signature):
            return True
        if hmac.compare_digest(expected_col, header_signature):
            return True
    except Exception:
        # Fall back to safe string equality if compare_digest fails (shouldn't)
        if expected_eq == header_signature or expected_col == header_signature:
            return True

    # no match
    return False


@app.post("/webhook")
async def webhook(request: Request, x_hub_signature_256: Optional[str] = Header(None)):
    # read raw body
    body = await request.body()

    # debug logging to help troubleshooting — remove or reduce in prod
    log.info("Received webhook POST (%d bytes) from %s", len(body), request.client.host if request.client else "unknown")
    if x_hub_signature_256:
        log.debug("Header X-Hub-Signature-256: %s", x_hub_signature_256)

    # verify signature
    if not await verify_signature(body, x_hub_signature_256):
        log.warning("Invalid or missing signature")
        raise HTTPException(status_code=401, detail="invalid signature")

    # parse JSON
    try:
        payload = await request.json()
    except Exception as e:
        log.error("Failed to parse JSON payload: %s", e)
        raise HTTPException(status_code=400, detail="invalid json")

    # minimal normalization for downstream processing
    event = normalize_event(payload)

    # fire-and-forget background processing
    asyncio.create_task(process_event_async(event))
    return {"received": True}


def normalize_event(payload: dict) -> dict:
    """
    Small normalization for Jenkins/GitHub-like payloads.
    Extend this to support other webhook shapes as needed.
    """
    if "build" in payload:
        build = payload.get("build", {})
        return {
            "source": "jenkins",
            "status": build.get("status"),
            "url": build.get("full_url") or build.get("url"),
            "logs": build.get("logs") or "",
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
    # fallback: include whole payload as logs (stringified) — be careful with secrets
    return {"source": "unknown", "status": None, "logs": str(payload), "metadata": payload}
