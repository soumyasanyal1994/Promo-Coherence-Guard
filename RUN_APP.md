# Promo Coherence Guard — Run Guide

You can run this app in **two ways**:

1. **Local Python** (venv + Streamlit)
2. **Docker**

Replace **`<project-root>`** below with the folder that contains `requirements.txt` and `promo_guard/` (for example `C:\Promo-Coherence-Guard\Promo-Coherence-Guard` on Windows, or `/path/to/Promo-Coherence-Guard` on macOS/Linux).

Open the UI at **http://localhost:8501** after the server or container starts.

---

## Option 1: Local Python

### Windows (PowerShell)

**First-time setup**

```powershell
Set-Location "<project-root>"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

If `Activate.ps1` is blocked by policy, run once in that window:  
`Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`

Optional: regenerate bundled sample files under `data/` (skip if samples are already present).

```powershell
python scripts\generate_synthetic_data.py
```

**Start the app**

```powershell
Set-Location "<project-root>"
.\.venv\Scripts\Activate.ps1
streamlit run promo_guard\app.py --server.port 8501
```

**Notes**

- In **PowerShell**, chain commands with **`;`**, not `&&`, on older Windows builds.
- Stop the app: **Ctrl+C** in that terminal.

---

### macOS / Linux (bash or zsh)

**First-time setup**

```bash
cd "<project-root>"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/generate_synthetic_data.py   # optional
```

**Start the app**

```bash
cd "<project-root>"
source .venv/bin/activate
streamlit run promo_guard/app.py --server.port 8501
```

Stop the app: **Ctrl+C**.

---

## Option 2: Docker

Docker commands are the same on **Windows**, **macOS**, and **Linux** if Docker Desktop (or Docker Engine) is installed and on your `PATH`. From the project root:

### Build image

```bash
cd "<project-root>"
docker build -t promo-coherence-guard .
```

On Windows PowerShell you can use the same `cd` and `docker` lines; use your real path, for example:

```powershell
Set-Location "C:\Promo-Coherence-Guard\Promo-Coherence-Guard"
docker build -t promo-coherence-guard .
```

### Run container (foreground)

```bash
docker run --rm -p 8501:8501 --name promo-coherence-guard promo-coherence-guard
```

Press **Ctrl+C** to stop. `--rm` removes the container when it exits.

### Run container (background)

```bash
docker run -d -p 8501:8501 --name promo-coherence-guard promo-coherence-guard
```

### Stop a detached container

```bash
docker stop promo-coherence-guard
docker rm promo-coherence-guard
```

### Rebuild after code changes

**macOS / Linux**

```bash
cd "<project-root>"
docker rm -f promo-coherence-guard 2>/dev/null || true
docker build -t promo-coherence-guard .
docker run -d -p 8501:8501 --name promo-coherence-guard promo-coherence-guard
```

**Windows (PowerShell)** — run each line; ignore errors if the container does not exist.

```powershell
Set-Location "<project-root>"
docker rm -f promo-coherence-guard
docker build -t promo-coherence-guard .
docker run -d -p 8501:8501 --name promo-coherence-guard promo-coherence-guard
```

### Check status and logs

```bash
docker ps --filter "name=promo-coherence-guard"
docker logs -f promo-coherence-guard
```

### Full cleanup (optional)

**macOS / Linux**

```bash
docker rm -f promo-coherence-guard 2>/dev/null || true
docker rmi promo-coherence-guard
```

**Windows (PowerShell)**

```powershell
docker rm -f promo-coherence-guard
docker rmi promo-coherence-guard
```

### Docker prerequisites

- **Docker Desktop** (Windows/macOS) or **Docker Engine** (Linux) installed and running.
- Port **8501** free on the host.

---

## If port 8501 is already in use

**Windows (PowerShell)**

```powershell
Get-NetTCPConnection -LocalPort 8501 | Select-Object OwningProcess
Stop-Process -Id <PID> -Force
```

**macOS / Linux**

```bash
lsof -i :8501
kill -9 <PID>
```

Then start Streamlit or Docker again.

---

## Frontend vs backend

This project runs as a **single Streamlit** app: the UI, deterministic conflict engine, and optional Gemini calls all run in that process. There is no separate backend server.

---

## Audit log

Successful scans append one JSON line per run to **`logs/scans.jsonl`**. That file may show as modified in Git after local use; see `.gitignore` if you want to exclude it from commits.
