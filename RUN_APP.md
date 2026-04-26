# Promo Coherence Guard - Run Guide

You can run this app in **two ways**:

1. Normal local Python setup
2. Dockerized setup

---

## Option 1: Run normally (local Python)

### Full setup and run (first time)

```bash
cd "/Users/soumya/Documents/AIHack"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/generate_synthetic_data.py
streamlit run promo_guard/app.py --server.port 8501
```

### Quick start (after initial setup)

```bash
cd "/Users/soumya/Documents/AIHack"
source .venv/bin/activate
streamlit run promo_guard/app.py --server.port 8501
```

Open in browser:

- `http://localhost:8501`

---

## Option 2: Run with Docker

### Build image

```bash
cd "/Users/soumya/Documents/AIHack"
docker build -t promo-coherence-guard .
```

### Run container

```bash
docker run --rm -p 8501:8501 --name promo-coherence-guard promo-coherence-guard
```

Open in browser:

- `http://localhost:8501`

### Stop container

Press `Ctrl + C` in the terminal where `docker run` is active.

If running detached:

```bash
docker stop promo-coherence-guard
```

### Rebuild after code changes

```bash
cd "/Users/soumya/Documents/AIHack"
docker rm -f promo-coherence-guard >/dev/null 2>&1 || true
docker build -t promo-coherence-guard .
docker run -d -p 8501:8501 --name promo-coherence-guard promo-coherence-guard
```

---

## Docker Setup Details (separate section)

### Prerequisites

- Docker Desktop installed and running
- Port `8501` available on your machine
- Project path: `/Users/soumya/Documents/AIHack`

### One-time setup

```bash
cd "/Users/soumya/Documents/AIHack"
docker build -t promo-coherence-guard .
```

### Start app in background (recommended)

```bash
docker run -d -p 8501:8501 --name promo-coherence-guard promo-coherence-guard
```

### Check container status

```bash
docker ps --filter "name=promo-coherence-guard"
```

### Check logs

```bash
docker logs -f promo-coherence-guard
```

### Stop and remove container

```bash
docker stop promo-coherence-guard
docker rm promo-coherence-guard
```

### Full cleanup (optional)

```bash
docker rm -f promo-coherence-guard >/dev/null 2>&1 || true
docker rmi promo-coherence-guard
```

Open in browser:

- `http://localhost:8501`

---

## If port 8501 is already in use

```bash
lsof -i :8501
kill -9 <PID>
```

Then rerun either local or Docker command.

---

## Frontend vs Backend note

This project currently runs as a single Streamlit application.

- **Frontend**: Streamlit UI
- **Backend logic**: deterministic engine + Gemini calls

There is no separate backend server process yet, so starting Streamlit starts both layers.
