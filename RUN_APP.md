# Promo Coherence Guard — Run Guide

The app now runs as:

- **FastAPI backend**
- **Vue.js frontend** (served by FastAPI)

Open the app at **http://localhost:8501**.

## Local run

### Windows (PowerShell)

```powershell
Set-Location "<project-root>"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python scripts\generate_synthetic_data.py   # optional
uvicorn promo_guard.app:app --host 0.0.0.0 --port 8501 --reload
```

### Windows quick start (batch)

```powershell
Set-Location "<project-root>"
.\startup.bat
```

### macOS / Linux

```bash
cd "<project-root>"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/generate_synthetic_data.py   # optional
uvicorn promo_guard.app:app --host 0.0.0.0 --port 8501 --reload
```

### macOS / Linux quick start (shell)

```bash
cd "<project-root>"
chmod +x startup.sh
./startup.sh
```

`startup.sh` and `startup.bat` support optional environment variables:

- `HOST` (default `0.0.0.0`)
- `PORT` (default `8501`)
- `RELOAD` (`1` to enable auto-reload)
- `GENERATE_SAMPLE_DATA` (`1` to regenerate bundled sample data)

## Run mode for this project

Use local startup scripts only (no Docker):

- Windows: `startup.bat`
- macOS/Linux: `startup.sh`

## Custom Gemini endpoint (corporate/internal)

In the UI sidebar, when provider is **Google Gemini**, you can set **Custom Gemini endpoint (optional)**.

- If provided, scans use that endpoint.
- If empty, scans use the default public Gemini endpoint.
- For LiteLLM/OpenAI-compatible proxies, use one of these endpoint styles:
  - `http://localhost:4000`
  - `https://<your-proxy-host>/v1`
  - `https://<your-proxy-host>/v1/chat/completions`
- Custom endpoint requests now use explicit timeouts to avoid long "pending" scans.

## Audit log

Each successful scan appends one JSON line to `logs/scans.jsonl`.
