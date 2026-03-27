import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("nubarium_gateway")

APP_VERSION = "4.0.0"
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
REQUESTS_FILE = DATA_DIR / "requests_store.json"

NUBARIUM_USER = os.getenv("NUBARIUM_USER", "")
NUBARIUM_PASS = os.getenv("NUBARIUM_PASS", "")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
WEBHOOK_TOKEN = os.getenv("WEBHOOK_TOKEN", "cambia-esto-en-produccion")
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()]

# Nota: RENAPO e IMSS/ISSSTE vienen de tu versión v3.
# SAT e INE se dejan como "por confirmar".
NUBARIUM_URLS = {
    "renapo": "https://curp.nubarium.com/renapo/v3/valida_curp",
    "imss_nss": "https://api.nubarium.com/imss/why/v1/obtener_nss",
    "imss_empleo": "https://api.nubarium.com/imex/se/v1/employment-info-imss",
    "issste": "https://api.nubarium.com/issste/v2/obtener_historial",
    "sat": "https://services.nubarium.com/biometricos/sat/v1/getRfc",
    "ine": "https://services.nubarium.com/biometricos/ine/v1/getIne",
}

SERVICE_CONFIG = {
    "renapo": {
        "required_fields": ["curp"],
        "supports_webhook": True,
        "webhook_field": "url",
        "frontend_async_default": False,
    },
    "sat": {
        "required_fields": ["rfc"],
        "supports_webhook": False,
    },
    "imss_nss": {
        "required_fields": ["curp"],
        "supports_webhook": True,
        "webhook_field": "uri",
        "headers_field": "encabezados",
        "frontend_async_default": True,
    },
    "imss_empleo": {
        "required_fields": ["curp", "nss"],
        "supports_webhook": True,
        "webhook_field": "uri",
        "headers_field": "encabezados",
        "frontend_async_default": True,
    },
    "issste": {
        "required_fields": ["curp"],
        "supports_webhook": True,
        "webhook_field": "uri",
        "headers_field": "encabezados",
        "frontend_async_default": True,
    },
    "ine": {
        "required_fields": ["cic", "ocr"],
        "supports_webhook": False,
    },
}


class ConsultaRequest(BaseModel):
    servicio: str
    curp: Optional[str] = None
    generarRFC: Optional[bool] = None
    documento: Optional[str] = None
    url: Optional[str] = None
    rfc: Optional[str] = None
    nss: Optional[str] = None
    uri: Optional[str] = None
    encabezados: Optional[Dict[str, Any]] = None
    cic: Optional[str] = None
    ocr: Optional[str] = None
    identificador: Optional[str] = None
    anio_registro: Optional[str] = None
    numero_emision: Optional[str] = None
    vigencia: Optional[str] = None
    force_async: Optional[bool] = Field(default=None, description="Si true, el backend forzará callback/webhook cuando el servicio lo soporte")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_store() -> Dict[str, Any]:
    if not REQUESTS_FILE.exists():
        return {}
    try:
        return json.loads(REQUESTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("No se pudo leer requests_store.json; se recreará")
        return {}


def save_store(store: Dict[str, Any]) -> None:
    REQUESTS_FILE.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")


def save_request_record(request_id: str, record: Dict[str, Any]) -> None:
    store = load_store()
    store[request_id] = record
    save_store(store)


def update_request_record(request_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    store = load_store()
    record = store.get(request_id)
    if not record:
        raise KeyError(f"request_id no encontrado: {request_id}")
    record.update(patch)
    store[request_id] = record
    save_store(store)
    return record


def get_request_record(request_id: str) -> Dict[str, Any]:
    store = load_store()
    record = store.get(request_id)
    if not record:
        raise KeyError(f"request_id no encontrado: {request_id}")
    return record


def normalize_service(servicio: str) -> str:
    return (servicio or "").strip().lower()


def validate_required_fields(servicio: str, payload: Dict[str, Any]) -> None:
    cfg = SERVICE_CONFIG.get(servicio, {})
    required_fields = cfg.get("required_fields", [])
    missing = [field for field in required_fields if not payload.get(field)]
    if missing:
        raise HTTPException(status_code=400, detail=f"Faltan campos requeridos para {servicio}: {', '.join(missing)}")


def webhook_url_for(service: str, request_id: str) -> str:
    return f"{PUBLIC_BASE_URL}/webhooks/nubarium/{service}/{request_id}?token={WEBHOOK_TOKEN}"


def build_payload(req: ConsultaRequest, request_id: str) -> Dict[str, Any]:
    s = normalize_service(req.servicio)
    cfg = SERVICE_CONFIG.get(s, {})
    force_async = req.force_async if req.force_async is not None else cfg.get("frontend_async_default", False)

    if s == "renapo":
        payload: Dict[str, Any] = {"curp": req.curp}
        if req.generarRFC is not None:
            payload["generarRFC"] = req.generarRFC
        if req.documento is not None:
            payload["documento"] = req.documento
        if req.url:
            payload["url"] = req.url
        elif force_async and cfg.get("supports_webhook"):
            payload["url"] = webhook_url_for(s, request_id)
        return {k: v for k, v in payload.items() if v is not None}

    if s == "sat":
        return {"rfc": req.rfc}

    if s == "imss_nss":
        payload = {"curp": req.curp}
        if req.uri:
            payload["uri"] = req.uri
        elif force_async and cfg.get("supports_webhook"):
            payload["uri"] = webhook_url_for(s, request_id)
        if req.encabezados:
            payload["encabezados"] = req.encabezados
        elif force_async and cfg.get("headers_field"):
            payload["encabezados"] = {"x-request-id": request_id}
        return {k: v for k, v in payload.items() if v is not None}

    if s == "imss_empleo":
        payload = {"curp": req.curp, "nss": req.nss}
        if req.uri:
            payload["uri"] = req.uri
        elif force_async and cfg.get("supports_webhook"):
            payload["uri"] = webhook_url_for(s, request_id)
        if req.encabezados:
            payload["encabezados"] = req.encabezados
        elif force_async and cfg.get("headers_field"):
            payload["encabezados"] = {"x-request-id": request_id}
        return {k: v for k, v in payload.items() if v is not None}

    if s == "issste":
        payload = {"curp": req.curp}
        if req.nss:
            payload["nss"] = req.nss
        if req.uri:
            payload["uri"] = req.uri
        elif force_async and cfg.get("supports_webhook"):
            payload["uri"] = webhook_url_for(s, request_id)
        if req.encabezados:
            payload["encabezados"] = req.encabezados
        elif force_async and cfg.get("headers_field"):
            payload["encabezados"] = {"x-request-id": request_id}
        return {k: v for k, v in payload.items() if v is not None}

    if s == "ine":
        payload = {
            "cic": req.cic,
            "ocr": req.ocr,
            "identificador": req.identificador,
            "anio_registro": req.anio_registro,
            "numero_emision": req.numero_emision,
            "vigencia": req.vigencia,
        }
        return {k: v for k, v in payload.items() if v is not None}

    raise HTTPException(status_code=400, detail=f"Servicio no reconocido: {s}")


async def parse_response(response: httpx.Response) -> Dict[str, Any]:
    content_type = response.headers.get("content-type", "")
    try:
        data = response.json()
        return {"kind": "json", "data": data, "content_type": content_type}
    except Exception:
        text = response.text
        return {"kind": "text", "data": text, "content_type": content_type}


app = FastAPI(
    title="Nubarium Gateway",
    description="Gateway con soporte de respuestas síncronas y webhooks",
    version=APP_VERSION,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS if ALLOWED_ORIGINS != ["*"] else ["*"],
    allow_credentials=False if ALLOWED_ORIGINS == ["*"] else True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    return {
        "name": "Nubarium Gateway",
        "version": APP_VERSION,
        "status": "ok",
        "time": now_iso(),
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": APP_VERSION,
        "services": list(NUBARIUM_URLS.keys()),
        "public_base_url": PUBLIC_BASE_URL,
        "credentials_configured": bool(NUBARIUM_USER and NUBARIUM_PASS),
        "time": now_iso(),
    }


@app.post("/api/consultar")
async def consultar(req: ConsultaRequest):
    servicio = normalize_service(req.servicio)
    if servicio not in NUBARIUM_URLS:
        raise HTTPException(status_code=400, detail=f"Servicio no soportado: {servicio}")

    if not NUBARIUM_USER or not NUBARIUM_PASS:
        raise HTTPException(status_code=500, detail="Credenciales de Nubarium no configuradas")

    request_id = str(uuid.uuid4())
    payload = build_payload(req, request_id)
    validate_required_fields(servicio, payload)

    cfg = SERVICE_CONFIG.get(servicio, {})
    expected_async = False
    if cfg.get("supports_webhook"):
        if payload.get("url") or payload.get("uri"):
            expected_async = True

    record = {
        "request_id": request_id,
        "servicio": servicio,
        "status": "pending" if expected_async else "processing",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "request_payload": payload,
        "provider_url": NUBARIUM_URLS[servicio],
        "expected_async": expected_async,
        "provider_response": None,
        "provider_http_status": None,
        "error": None,
        "webhook_payload": None,
    }
    save_request_record(request_id, record)

    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.post(
                NUBARIUM_URLS[servicio],
                json=payload,
                auth=(NUBARIUM_USER, NUBARIUM_PASS),
                headers={"Content-Type": "application/json"},
            )

        parsed = await parse_response(response)
        update_request_record(
            request_id,
            {
                "provider_http_status": response.status_code,
                "provider_response": parsed["data"],
                "updated_at": now_iso(),
            },
        )

        if response.is_success:
            if expected_async:
                update_request_record(
                    request_id,
                    {
                        "status": "pending",
                        "updated_at": now_iso(),
                    },
                )
                return {
                    "ok": True,
                    "mode": "async",
                    "status": "pending",
                    "request_id": request_id,
                    "servicio": servicio,
                    "message": "Consulta enviada a Nubarium. Esperando webhook.",
                    "poll_url": f"/api/resultados/{request_id}",
                    "provider_ack": parsed["data"],
                }

            update_request_record(
                request_id,
                {
                    "status": "completed",
                    "updated_at": now_iso(),
                },
            )
            return {
                "ok": True,
                "mode": "sync",
                "status": "completed",
                "request_id": request_id,
                "servicio": servicio,
                "data": parsed["data"],
            }

        update_request_record(
            request_id,
            {
                "status": "error",
                "error": parsed["data"],
                "updated_at": now_iso(),
            },
        )
        return JSONResponse(
            status_code=response.status_code,
            content={
                "ok": False,
                "status": "error",
                "request_id": request_id,
                "servicio": servicio,
                "provider_status_code": response.status_code,
                "error": parsed["data"],
            },
        )

    except httpx.TimeoutException:
        update_request_record(
            request_id,
            {"status": "error", "error": "Timeout al conectar con Nubarium", "updated_at": now_iso()},
        )
        raise HTTPException(status_code=504, detail="Timeout al conectar con Nubarium")
    except Exception as exc:
        update_request_record(
            request_id,
            {"status": "error", "error": str(exc), "updated_at": now_iso()},
        )
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/resultados/{request_id}")
def obtener_resultado(request_id: str):
    try:
        record = get_request_record(request_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="request_id no encontrado")

    return {
        "ok": record["status"] != "error",
        "request_id": record["request_id"],
        "servicio": record["servicio"],
        "status": record["status"],
        "expected_async": record["expected_async"],
        "created_at": record["created_at"],
        "updated_at": record["updated_at"],
        "provider_http_status": record["provider_http_status"],
        "provider_response": record["provider_response"],
        "webhook_payload": record["webhook_payload"],
        "error": record["error"],
    }


@app.post("/webhooks/nubarium/{servicio}/{request_id}")
async def recibir_webhook(servicio: str, request_id: str, request: Request, token: str):
    if token != WEBHOOK_TOKEN:
        raise HTTPException(status_code=401, detail="token inválido")

    servicio = normalize_service(servicio)

    try:
        record = get_request_record(request_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="request_id no encontrado")

    if record["servicio"] != servicio:
        raise HTTPException(status_code=400, detail="servicio no coincide con request_id")

    try:
        payload = await request.json()
    except Exception:
        raw_body = await request.body()
        payload = {"raw": raw_body.decode("utf-8", errors="ignore")}

    updated = update_request_record(
        request_id,
        {
            "status": "completed",
            "webhook_payload": payload,
            "updated_at": now_iso(),
        },
    )

    logger.info("Webhook recibido | servicio=%s | request_id=%s", servicio, request_id)

    return {
        "ok": True,
        "message": "Webhook recibido correctamente",
        "request_id": request_id,
        "status": updated["status"],
    }