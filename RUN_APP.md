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

## Docker run

```bash
cd "<project-root>"
docker build -t promo-coherence-guard .
docker run --rm -p 8501:8501 --name promo-coherence-guard promo-coherence-guard
```

## Custom Gemini endpoint (corporate/internal)

In the UI sidebar, when provider is **Google Gemini**, you can set **Custom Gemini endpoint (optional)**.

- If provided, scans use that endpoint.
- If empty, scans use the default public Gemini endpoint.

## Audit log

Each successful scan appends one JSON line to `logs/scans.jsonl`.
