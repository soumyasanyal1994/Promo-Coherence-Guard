"""Promo Coherence Guard - FastAPI backend + Vue.js frontend."""
from __future__ import annotations

import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from promo_guard.engine import (
    conflicts_to_dataframe,
    detect_conflicts,
    load_pricing_csv,
    load_pricing_file,
    load_promotions_jsonl,
)
from promo_guard.llm_client import CLAUDE_HAIKU_MODEL, batch_explain_with_provider

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
LOGS = ROOT / "logs"
DEFAULT_PROMO = DATA / "sample_promotions.jsonl"
DEFAULT_PRICE = DATA / "sample_pricing.csv"
TAXONOMY = DATA / "conflict_taxonomy.json"
STATIC_DIR = ROOT / "promo_guard" / "static"
DEMO_AS_OF = datetime(2026, 11, 29, 12, 0, 0, tzinfo=timezone.utc)

GEMINI_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-1.5-flash",
]
CLAUDE_MODELS = [CLAUDE_HAIKU_MODEL]

app = FastAPI(title="Promo Coherence Guard")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_temp_upload(data: bytes, suffix: str) -> Path:
    with tempfile.NamedTemporaryFile(delete=False, prefix="pcg_", suffix=suffix) as tf:
        tf.write(data)
        return Path(tf.name)


def _append_audit(entry: dict[str, Any]) -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    path = LOGS / "scans.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _normalize_utc_iso(as_of: str) -> datetime:
    parsed = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/_stcore/health")
def stcore_health() -> dict[str, str]:
    """Compatibility endpoint for stale Streamlit tabs/extensions."""
    return {"status": "ok"}


@app.get("/_stcore/host-config")
def stcore_host_config() -> dict[str, Any]:
    """Compatibility endpoint for stale Streamlit tabs/extensions."""
    return {"allowedOrigins": ["*"], "useExternalAuthToken": False}


@app.websocket("/_stcore/stream")
async def stcore_stream(websocket: WebSocket) -> None:
    """Compatibility websocket for stale Streamlit tabs/extensions."""
    await websocket.accept()
    await websocket.send_json({"status": "ok", "message": "Streamlit stream compatibility endpoint"})
    await websocket.close()


@app.get("/api/config")
def config() -> dict[str, Any]:
    taxonomy_count = 0
    if TAXONOMY.exists():
        taxonomy = json.loads(TAXONOMY.read_text(encoding="utf-8"))
        taxonomy_count = len(taxonomy.get("mvp_active_types", []))
    return {
        "llm_providers": {
            "gemini": {"label": "Google Gemini", "models": GEMINI_MODELS},
            "claude": {"label": "Anthropic Claude", "models": CLAUDE_MODELS},
        },
        "channels": ["web", "app", "pos"],
        "default_as_of_utc": DEMO_AS_OF.isoformat(),
        "default_horizon_days": 7,
        "taxonomy_count": taxonomy_count,
    }


@app.post("/api/scan")
async def scan(
    channel: str = Form("web"),
    as_of_utc: str = Form(DEMO_AS_OF.isoformat()),
    horizon_days: int = Form(7),
    use_samples: bool = Form(True),
    use_llm_in_scan: bool = Form(True),
    llm_provider: str = Form("gemini"),
    api_key: str = Form(""),
    custom_endpoint: str = Form(""),
    model_name: str = Form("gemini-2.5-flash"),
    max_llm_conflicts: int = Form(35),
    promos_file: UploadFile | None = File(None),
    pricing_file: UploadFile | None = File(None),
) -> dict[str, Any]:
    try:
        as_of = _normalize_utc_iso(as_of_utc)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid as-of datetime.") from exc

    if promos_file is not None:
        promo_bytes = await promos_file.read()
        promos_path = _write_temp_upload(promo_bytes, ".jsonl")
        try:
            promos = load_promotions_jsonl(promos_path)
        finally:
            promos_path.unlink(missing_ok=True)
        promos_hash = _sha256_bytes(promo_bytes)
    elif use_samples and DEFAULT_PROMO.exists():
        promos = load_promotions_jsonl(DEFAULT_PROMO)
        promos_hash = _sha256_bytes(DEFAULT_PROMO.read_bytes())
    else:
        raise HTTPException(status_code=400, detail="Provide a promotions file or enable bundled samples.")

    if pricing_file is not None:
        price_bytes = await pricing_file.read()
        suffix = Path(pricing_file.filename or "").suffix.lower()
        if suffix not in (".csv", ".xlsx", ".xls"):
            suffix = ".csv"
        price_path = _write_temp_upload(price_bytes, suffix)
        try:
            pricing = load_pricing_file(price_path)
        finally:
            price_path.unlink(missing_ok=True)
        pricing_hash = _sha256_bytes(price_bytes)
    elif use_samples and DEFAULT_PRICE.exists():
        pricing = load_pricing_csv(DEFAULT_PRICE)
        pricing_hash = _sha256_bytes(DEFAULT_PRICE.read_bytes())
    else:
        raise HTTPException(status_code=400, detail="Provide a pricing file or enable bundled samples.")

    conflicts = detect_conflicts(
        promos,
        pricing,
        channel=channel,
        as_of=as_of,
        horizon_days=int(horizon_days),
    )
    df = conflicts_to_dataframe(conflicts)

    llm_used = False
    llm_warning = ""
    llm_model_used = ""
    llm_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    llm_provider_used = llm_provider.strip().lower() or "gemini"
    if use_llm_in_scan and not df.empty:
        if not api_key:
            llm_warning = "LLM is enabled but API key is missing. Showing deterministic results only."
        else:
            rows = df.to_dict("records")
            cap = int(max_llm_conflicts)
            texts, llm_model_used, llm_usage = batch_explain_with_provider(
                rows,
                provider=llm_provider_used,
                api_key=api_key,
                model_name=model_name,
                custom_endpoint=custom_endpoint,
                max_conflicts=cap,
                chunk_size=2,
            )
            df = df.copy()
            df["llm_narrative"] = texts
            llm_used = True
            if len(rows) > cap:
                llm_warning = (
                    f"Gemini ran on the first {cap} conflicts only. "
                    "Increase max LLM conflicts if needed."
                )

    audit = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "as_of_utc": as_of.isoformat(),
        "channel": channel,
        "promotions_sha256": promos_hash,
        "pricing_sha256": pricing_hash,
        "conflict_count": int(len(df)),
        "promotion_count": len(promos),
        "sku_count": len(pricing),
    }
    _append_audit(audit)

    records = df.where(pd.notna(df), None).to_dict("records")
    return {
        "ok": True,
        "conflicts": records,
        "audit": audit,
        "llm_used": llm_used,
        "llm_provider_used": llm_provider_used,
        "llm_model_used": llm_model_used or model_name,
        "llm_usage": llm_usage,
        "llm_warning": llm_warning,
        "scan_message": f"Scan complete - {len(records)} conflict(s) logged.",
    }
